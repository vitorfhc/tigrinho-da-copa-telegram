"""Bolãozinho commands + inline pickers/cards (Feature 7 / §22).

Read/join commands are open to everyone; management commands are creator/admin-only — every
management callback re-verifies the actor before acting (F11). The add-games picker is
identity-based: a toggle writes membership to the DB immediately and re-renders, so there is no
position drift (F18). All callbacks are stateless via ``callback_data`` (≤ 64 bytes, §3).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tigrinho import tournament_service as svc
from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.callbacks import TournamentAction, TournamentAddToggle, decode
from tigrinho.bot.keyboards import (
    announcement_keyboard,
    tournament_add_picker_keyboard,
    tournament_card_keyboard,
    tournament_join_card_keyboard,
    tournament_join_list_keyboard,
    tournament_list_keyboard,
)
from tigrinho.bot.messaging import safe_edit_text
from tigrinho.bot.runtime import AnyApplication, AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import Game, Tournament, TournamentStatus, utcnow
from tigrinho.db.repositories import GameRepository, PlayerRepository, TournamentRepository
from tigrinho.domain.text_pt import (
    entry_card_text,
    entry_confirmed_text,
    escape,
    format_kickoff_short,
    format_money_cents,
    tournament_announcement_text,
    tournament_card_text,
    tournament_details_text,
    tournament_list_text,
)
from tigrinho.domain.tournament import (
    parse_create_args,
    parse_price_to_cents,
    pot_cents,
    prize_cents,
)
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.tournament_handlers")

_CRIAR_USAGE = (
    "Uso: <code>/bolaozinho_criar Nome do bolão | preço</code>\n"
    "Exemplo: <code>/bolaozinho_criar Oitavas de final | 10</code>"
)
_PRECO_USAGE = "Uso: <code>/bolaozinho_preco &lt;id&gt; &lt;preço&gt;</code>"
_NOT_FOUND = "Não encontrei esse bolãozinho. 🤔"
_PICKER_PROMPT = "🎮 Toque para adicionar/remover jogos do bolãozinho:"
_MAX_PICKER_GAMES = 10
_MAX_STANDINGS = 15


def _display_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "Tigrinho"
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


def _game_triples(games: list[Game]) -> list[tuple[str, str, datetime]]:
    return [(g.home_team_name, g.away_team_name, g.kickoff_local) for g in games]


# --- render helpers (text + keyboard) ---------------------------------------------------------
def _render_card(
    session: Session, tournament: Tournament, settings: Settings
) -> tuple[str, InlineKeyboardMarkup | None]:
    repo = TournamentRepository(session)
    text = tournament_card_text(
        tournament_id=tournament.id,
        name=tournament.name,
        status=tournament.status,
        entry_price_cents=tournament.entry_price_cents,
        games=_game_triples(repo.list_games(tournament.id)),
        n_entrants=repo.count_entries(tournament.id),
        currency=settings.tournament_currency,
        decimals=settings.tournament_currency_decimals,
    )
    return text, tournament_card_keyboard(tournament.id, tournament.status)


def _render_add_picker(
    session: Session, tournament: Tournament, now: datetime
) -> tuple[str, InlineKeyboardMarkup]:
    member = set(TournamentRepository(session).list_game_ids(tournament.id))
    upcoming = GameRepository(session).list_upcoming(now)[:_MAX_PICKER_GAMES]
    items = [
        (
            g.fixture_id,
            f"{g.home_team_name} x {g.away_team_name} · {format_kickoff_short(g.kickoff_local)}",
            g.fixture_id in member,
        )
        for g in upcoming
    ]
    return _PICKER_PROMPT, tournament_add_picker_keyboard(tournament.id, items)


def _render_join_card(
    session: Session, tournament: Tournament, settings: Settings
) -> tuple[str, InlineKeyboardMarkup]:
    repo = TournamentRepository(session)
    n = repo.count_entries(tournament.id)
    price = tournament.entry_price_cents
    text = entry_card_text(
        name=tournament.name,
        entry_price_cents=price,
        pot_cents=pot_cents(n, price),
        prize_cents=prize_cents(n, price),
        n_entrants=n,
        games=_game_triples(repo.list_games(tournament.id)),
        currency=settings.tournament_currency,
        decimals=settings.tournament_currency_decimals,
    )
    entry_label = format_money_cents(
        price, currency=settings.tournament_currency, decimals=settings.tournament_currency_decimals
    )
    return text, tournament_join_card_keyboard(tournament.id, entry_label)


def _render_details(
    session: Session, tournament: Tournament, settings: Settings, viewer_id: int
) -> str:
    repo = TournamentRepository(session)
    players = PlayerRepository(session)
    games = repo.list_games(tournament.id)
    game_rows = [
        (g.home_team_name, g.away_team_name, g.kickoff_local, g.home_goals_90, g.away_goals_90)
        for g in games
    ]
    ranked = sorted(repo.standings(tournament.id).items(), key=lambda kv: (-kv[1], kv[0]))
    standings = [
        ((p.display_name if (p := players.get(tid)) is not None else str(tid)), points)
        for tid, points in ranked[:_MAX_STANDINGS]
    ]
    n = repo.count_entries(tournament.id)
    price = tournament.entry_price_cents
    return tournament_details_text(
        tournament_id=tournament.id,
        name=tournament.name,
        status=tournament.status,
        entry_price_cents=price,
        pot_cents=pot_cents(n, price),
        prize_cents=prize_cents(n, price),
        n_entrants=n,
        games=game_rows,
        standings=standings,
        you_entered=repo.is_entered(tournament.id, viewer_id),
        currency=settings.tournament_currency,
        decimals=settings.tournament_currency_decimals,
    )


def _list_payload(session: Session, settings: Settings) -> tuple[str, InlineKeyboardMarkup | None]:
    repo = TournamentRepository(session)
    tournaments = repo.list_all()
    items = [
        (
            t.id,
            t.name,
            t.status,
            pot_cents(repo.count_entries(t.id), t.entry_price_cents),
            repo.count_entries(t.id),
        )
        for t in tournaments
    ]
    text = tournament_list_text(
        items,
        currency=settings.tournament_currency,
        decimals=settings.tournament_currency_decimals,
    )
    keyboard = (
        tournament_list_keyboard([(t.id, f"#{t.id} {t.name}") for t in tournaments])
        if tournaments
        else None
    )
    return text, keyboard


async def _post_open_announcement(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    tournament: Tournament,
    games: list[Game],
) -> None:
    text = tournament_announcement_text(
        name=tournament.name,
        entry_price_cents=tournament.entry_price_cents,
        games=_game_triples(games),
        currency=settings.tournament_currency,
        decimals=settings.tournament_currency_decimals,
    )
    keyboard = announcement_keyboard(
        [(g.fixture_id, f"{g.home_team_name} x {g.away_team_name}") for g in games],
        settings.bot_username,
    )
    try:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except TelegramError as exc:
        _log.error("tournament_open_announce_failed", tournament_id=tournament.id, error=str(exc))
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Bolãozinho #{tournament.id} aberto, mas falhou ao anunciar no grupo: "
            f"<code>{escape(str(exc))}</code>",
        )


# --- command handlers -------------------------------------------------------------------------
async def cmd_criar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bolaozinho_criar Nome | preço — create a DRAFT and show its management card."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    try:
        name, price = parse_create_args(" ".join(context.args or []))
    except ValueError:
        await message.reply_text(_CRIAR_USAGE, parse_mode=ParseMode.HTML)
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        tournament = svc.create_tournament(
            session, name=name, entry_price_cents=price, created_by=user.id
        )
        text, keyboard = _render_card(session, tournament, app_context.settings)
        session.commit()
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_preco(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bolaozinho_preco <id> <preço> — adjust the entry price (creator/admin, pre-entry)."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    args = context.args or []
    try:
        tournament_id = int(args[0])
        price = parse_price_to_cents(args[1])
    except (IndexError, ValueError):
        await message.reply_text(_PRECO_USAGE, parse_mode=ParseMode.HTML)
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            await message.reply_text(_NOT_FOUND)
            return
        try:
            svc.require_manage(tournament, user.id, app_context.settings.admin_user_id)
            svc.set_price(session, tournament, price, now=utcnow())
        except svc.TournamentError as exc:
            await message.reply_text(exc.message)
            return
        text, keyboard = _render_card(session, tournament, app_context.settings)
        session.commit()
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_abrir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bolaozinho_abrir <id> — publish (creator/admin); announces it to the group."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    args = context.args or []
    try:
        tournament_id = int(args[0])
    except (IndexError, ValueError):
        await message.reply_text(
            "Uso: <code>/bolaozinho_abrir &lt;id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            await message.reply_text(_NOT_FOUND)
            return
        try:
            svc.require_manage(tournament, user.id, app_context.settings.admin_user_id)
            svc.open_tournament(session, tournament, now=utcnow())
        except svc.TournamentError as exc:
            await message.reply_text(exc.message)
            return
        games = TournamentRepository(session).list_games(tournament.id)
        text, keyboard = _render_card(session, tournament, app_context.settings)
        session.commit()
    await _post_open_announcement(context, app_context.settings, tournament, games)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bolaozinhos — list every bolãozinho with status, pot, and entrant count."""
    message = update.effective_message
    if message is None:
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        text, keyboard = _list_payload(session, app_context.settings)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bolaozinho [id] — details for one (or the list when no id is given)."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    args = context.args or []
    app_context = get_app_context(context.application)
    if not args:
        await cmd_list(update, context)
        return
    try:
        tournament_id = int(args[0])
    except ValueError:
        await message.reply_text(_NOT_FOUND)
        return
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            await message.reply_text(_NOT_FOUND)
            return
        text = _render_details(session, tournament, app_context.settings, user.id)
    await message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_entrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/entrar — list joinable bolãozinhos (or jump straight in when there's exactly one)."""
    message = update.effective_message
    if message is None:
        return
    app_context = get_app_context(context.application)
    now = utcnow()
    with app_context.session_factory() as session:
        repo = TournamentRepository(session)
        joinable = [
            t
            for t in repo.list_by_status(TournamentStatus.OPEN)
            if t.locked_at is None
            and (earliest := repo.earliest_kickoff(t.id)) is not None
            and now < earliest
        ]
        if not joinable:
            await message.reply_text("Nenhum bolãozinho aberto pra entrar agora. 🐯")
            return
        if len(joinable) == 1:
            text, keyboard = _render_join_card(session, joinable[0], app_context.settings)
            await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return
        list_kb = tournament_join_list_keyboard([(t.id, f"#{t.id} {t.name}") for t in joinable])
    await message.reply_text(
        "🏆 Escolha o bolãozinho pra entrar:", parse_mode=ParseMode.HTML, reply_markup=list_kb
    )


# --- callbacks --------------------------------------------------------------------------------
async def _load_manageable(
    query: CallbackQuery, session: Session, settings: Settings, tournament_id: int, actor_id: int
) -> Tournament | None:
    """Load a tournament for a management callback, answering a refusal if not allowed (F11)."""
    tournament = TournamentRepository(session).get(tournament_id)
    if tournament is None:
        await query.answer(_NOT_FOUND, show_alert=True)
        return None
    if not svc.can_manage(tournament, actor_id, settings.admin_user_id):
        await query.answer("Só quem criou o bolãozinho pode mexer nele.", show_alert=True)
        return None
    return tournament


async def on_tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatcher for all bolãozinho inline buttons (stateless via callback_data)."""
    query = update.callback_query
    user = update.effective_user
    if query is None or query.data is None or user is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida ou expirada.")
        return
    app_context = get_app_context(context.application)

    match data:
        case TournamentAddToggle(tournament_id, fixture_id):
            await _toggle_game(query, app_context, tournament_id, fixture_id, user.id)
        case TournamentAction("ba", tournament_id):
            await _open_add_picker(query, app_context, tournament_id, user.id)
        case TournamentAction("bd", tournament_id):
            await _back_to_card(query, app_context, tournament_id, user.id)
        case TournamentAction("bo", tournament_id):
            await _do_open(query, context, app_context, tournament_id, user.id)
        case TournamentAction("bx", tournament_id):
            await _do_cancel(query, app_context, tournament_id, user.id)
        case TournamentAction("bj", tournament_id):
            await _show_join_card(query, app_context, tournament_id)
        case TournamentAction("bk", tournament_id):
            await _do_join(query, app_context, update, tournament_id, user.id)
        case TournamentAction("bi", tournament_id):
            await _show_details(query, app_context, tournament_id, user.id)
        case _:  # pragma: no cover - pattern guarantees a tournament op
            await query.answer()


async def _open_add_picker(
    query: CallbackQuery, app_context: AppContext, tournament_id: int, actor_id: int
) -> None:
    with app_context.session_factory() as session:
        tournament = await _load_manageable(
            query, session, app_context.settings, tournament_id, actor_id
        )
        if tournament is None:
            return
        text, keyboard = _render_add_picker(session, tournament, utcnow())
    await query.answer()
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _toggle_game(
    query: CallbackQuery,
    app_context: AppContext,
    tournament_id: int,
    fixture_id: int,
    actor_id: int,
) -> None:
    with app_context.session_factory() as session:
        tournament = await _load_manageable(
            query, session, app_context.settings, tournament_id, actor_id
        )
        if tournament is None:
            return
        repo = TournamentRepository(session)
        if fixture_id in set(repo.list_game_ids(tournament.id)):
            try:
                svc.remove_game(session, tournament, fixture_id, now=utcnow())
            except svc.TournamentError as exc:
                await query.answer(exc.message, show_alert=True)
                return
        else:
            try:
                svc.add_game(session, tournament, fixture_id, now=utcnow())
            except svc.TournamentError as exc:
                await query.answer(exc.message, show_alert=True)
                return
        text, keyboard = _render_add_picker(session, tournament, utcnow())
        session.commit()
    await query.answer()
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _back_to_card(
    query: CallbackQuery, app_context: AppContext, tournament_id: int, actor_id: int
) -> None:
    with app_context.session_factory() as session:
        tournament = await _load_manageable(
            query, session, app_context.settings, tournament_id, actor_id
        )
        if tournament is None:
            return
        text, keyboard = _render_card(session, tournament, app_context.settings)
    await query.answer()
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _do_open(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    app_context: AppContext,
    tournament_id: int,
    actor_id: int,
) -> None:
    with app_context.session_factory() as session:
        tournament = await _load_manageable(
            query, session, app_context.settings, tournament_id, actor_id
        )
        if tournament is None:
            return
        try:
            svc.open_tournament(session, tournament, now=utcnow())
        except svc.TournamentError as exc:
            await query.answer(exc.message, show_alert=True)
            return
        games = TournamentRepository(session).list_games(tournament.id)
        text, keyboard = _render_card(session, tournament, app_context.settings)
        session.commit()
    await _post_open_announcement(context, app_context.settings, tournament, games)
    await query.answer("Bolãozinho aberto! 📣")
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _do_cancel(
    query: CallbackQuery, app_context: AppContext, tournament_id: int, actor_id: int
) -> None:
    with app_context.session_factory() as session:
        tournament = await _load_manageable(
            query, session, app_context.settings, tournament_id, actor_id
        )
        if tournament is None:
            return
        try:
            svc.cancel_tournament(session, tournament)
        except svc.TournamentError as exc:
            await query.answer(exc.message, show_alert=True)
            return
        text, keyboard = _render_card(session, tournament, app_context.settings)
        session.commit()
    await query.answer("Bolãozinho cancelado.")
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _show_join_card(
    query: CallbackQuery, app_context: AppContext, tournament_id: int
) -> None:
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            await query.answer(_NOT_FOUND, show_alert=True)
            return
        text, keyboard = _render_join_card(session, tournament, app_context.settings)
    await query.answer()
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _do_join(
    query: CallbackQuery,
    app_context: AppContext,
    update: Update,
    tournament_id: int,
    actor_id: int,
) -> None:
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            await query.answer(_NOT_FOUND, show_alert=True)
            return
        try:
            result = svc.join(
                session,
                tournament,
                telegram_id=actor_id,
                display_name=_display_name(update),
                now=utcnow(),
            )
        except svc.TournamentError as exc:
            await query.answer(exc.message, show_alert=True)
            return
        games = TournamentRepository(session).list_games(tournament.id)
        text = entry_confirmed_text(
            name=tournament.name,
            n_entrants=result.n_entrants,
            pot_cents=result.pot_cents,
            prize_cents=result.prize_cents,
            currency=app_context.settings.tournament_currency,
            decimals=app_context.settings.tournament_currency_decimals,
        )
        keyboard = announcement_keyboard(
            [(g.fixture_id, f"{g.home_team_name} x {g.away_team_name}") for g in games],
            app_context.settings.bot_username,
        )
        session.commit()
    await query.answer(
        "Você está no bolãozinho! ✅" if not result.already else "Você já estava dentro. 🐯"
    )
    await safe_edit_text(query, text, reply_markup=keyboard)


async def _show_details(
    query: CallbackQuery, app_context: AppContext, tournament_id: int, viewer_id: int
) -> None:
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            await query.answer(_NOT_FOUND, show_alert=True)
            return
        text = _render_details(session, tournament, app_context.settings, viewer_id)
    await query.answer()
    await safe_edit_text(query, text)


def register_tournament_handlers(application: AnyApplication) -> None:
    """Register bolãozinho commands + the callback dispatcher (before the bet wizard catch-all)."""
    application.add_handler(CommandHandler("bolaozinho_criar", cmd_criar))
    application.add_handler(CommandHandler("bolaozinho_preco", cmd_preco))
    application.add_handler(CommandHandler("bolaozinho_abrir", cmd_abrir))
    application.add_handler(CommandHandler("bolaozinhos", cmd_list))
    application.add_handler(CommandHandler("bolaozinho", cmd_show))
    application.add_handler(CommandHandler("entrar", cmd_entrar))
    application.add_handler(
        CallbackQueryHandler(on_tournament_callback, pattern="^(ba|bd|bo|bx|bj|bk|bi|bg):")
    )
