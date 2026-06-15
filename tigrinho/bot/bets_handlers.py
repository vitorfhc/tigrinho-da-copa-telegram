"""Bet placement flow: deep-link entry, /apostar wizard, /minhas_apostas, /jogos (§8.2).

The wizard is **stateless**: every inline button carries all it needs in ``callback_data`` (see
:mod:`tigrinho.bot.callbacks`), so there is no per-user conversation state to manage and the flow
survives restarts. Each step edits the same message in place. Bets close purely by time
(``now >= kickoff_utc``) — any create/edit/delete on a started game is rejected with no API call.

Design decision (2026-06-15, M6): a stateless ``CallbackQueryHandler`` wizard fully realizes the
spec's "all wizard state encoded in callback_data" principle (§3/§8.2) and is more robust than a
``ConversationHandler`` state machine; recorded in COMPLETION.md.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tigrinho.bot.callbacks import (
    BttsInput,
    Cancel,
    ChooseCategory,
    ChooseGame,
    DeleteBet,
    ExactScore,
    FirstTeamInput,
    HomeScore,
    OverUnderInput,
    WinnerInput,
    decode,
)
from tigrinho.bot.keyboards import (
    announcement_keyboard,
    away_score_keyboard,
    btts_keyboard,
    category_keyboard,
    first_team_keyboard,
    games_keyboard,
    home_score_keyboard,
    my_bets_keyboard,
    over_under_keyboard,
    winner_keyboard,
)
from tigrinho.bot.messaging import safe_edit_text
from tigrinho.bot.runtime import AnyApplication, AppContext, get_app_context
from tigrinho.db.models import Bet, Game, GameStatus, utcnow
from tigrinho.db.repositories import (
    BetRepository,
    GameRepository,
    PlayerRepository,
)
from tigrinho.domain.bets import (
    BetCategory,
    BttsPayload,
    ExactScorePayload,
    FirstTeamPayload,
    OverUnderPayload,
    Payload,
    WinnerPayload,
    parse_payload,
    serialize_payload,
)
from tigrinho.domain.text_pt import describe_bet, escape, format_kickoff_local, welcome_text

_CLOSED_MESSAGE = "⏰ Esse jogo já começou — as apostas estão fechadas."
_NOT_FOUND_MESSAGE = "Jogo não encontrado."


def _display_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "Tigrinho"
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


def _is_open(game: Game) -> bool:
    return game.status is GameStatus.SCHEDULED and game.kickoff_utc > utcnow()


def _game_label(game: Game) -> str:
    return f"{game.home_team_name} x {game.away_team_name}"


def _describe_stored(bet: Bet, game: Game) -> str:
    payload = parse_payload(BetCategory(bet.category), bet.payload_json)
    return describe_bet(payload, home_team=game.home_team_name, away_team=game.away_team_name)


def _existing_bets_text(session: Session, telegram_id: int, game: Game) -> str:
    bets = BetRepository(session).list_for_player_and_game(telegram_id, game.fixture_id)
    if not bets:
        return ""
    lines = [f"• {_describe_stored(bet, game)}" for bet in bets]
    return "\n\n<b>Seus palpites neste jogo:</b>\n" + "\n".join(lines)


def _category_prompt(session: Session, telegram_id: int, game: Game) -> str:
    return (
        f"🎯 <b>{escape(_game_label(game))}</b>\nEscolha a categoria do seu palpite:"
        + _existing_bets_text(session, telegram_id, game)
    )


async def _edit(
    query: CallbackQuery, text: str, *, keyboard: InlineKeyboardMarkup | None = None
) -> None:
    await safe_edit_text(query, text, reply_markup=keyboard)


# --- entry points ---------------------------------------------------------------------------


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — deep-link ``bet_<fixture_id>`` jumps into the wizard; otherwise welcome (§8.2)."""
    message = update.effective_message
    if message is None:
        return
    args = context.args or []
    payload = args[0] if args else ""
    if payload.startswith("bet_"):
        try:
            fixture_id = int(payload.removeprefix("bet_"))
        except ValueError:
            await message.reply_text(welcome_text(), parse_mode=ParseMode.HTML)
            return
        await _enter_wizard(update, context, fixture_id)
        return
    if payload == "apostar":
        # Deep link from the group "Apostar no privado" button → open the games picker.
        await _show_open_games(update, get_app_context(context.application))
        return
    await message.reply_text(welcome_text(), parse_mode=ParseMode.HTML)


async def apostar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/apostar — in DM list open games; in the group redirect to the private chat (§8.2)."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    app_context = get_app_context(context.application)
    if chat.type != ChatType.PRIVATE:
        url = f"https://t.me/{app_context.settings.bot_username}?start=apostar"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👉 Apostar no privado", url=url)]])
        await message.reply_text("Os palpites são no meu privado 👇", reply_markup=keyboard)
        return
    await _show_open_games(update, app_context)


async def _show_open_games(update: Update, app_context: AppContext) -> None:
    message = update.effective_message
    if message is None:
        return
    with app_context.session_factory() as session:
        games = GameRepository(session).list_upcoming(utcnow())
        items = [(g.fixture_id, _game_label(g)) for g in games]
    if not items:
        await message.reply_text("Não há jogos abertos para apostas no momento.")
        return
    await message.reply_text("Escolha um jogo para palpitar:", reply_markup=games_keyboard(items))


async def _enter_wizard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fixture_id: int
) -> None:
    """Auto-create the player and open the category step for ``fixture_id`` (§8.2)."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(user.id, _display_name(update))
        game = GameRepository(session).get(fixture_id)
        if game is None:
            session.commit()
            await message.reply_text(_NOT_FOUND_MESSAGE)
            return
        if not _is_open(game):
            session.commit()
            await message.reply_text(_CLOSED_MESSAGE)
            return
        text = _category_prompt(session, user.id, game)
        session.commit()
    await message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=category_keyboard(fixture_id)
    )


# --- callback dispatch ----------------------------------------------------------------------


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single dispatcher for all wizard inline buttons (stateless via callback_data)."""
    query = update.callback_query
    user = update.effective_user
    if query is None or query.data is None or user is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida ou expirada.")
        return
    await query.answer()
    app_context = get_app_context(context.application)

    match data:
        case Cancel():
            await _edit(query, "Ok, cancelado. Use /apostar quando quiser. 🐯")
        case ChooseGame(fixture_id):
            await _step_category(query, app_context, user.id, fixture_id)
        case ChooseCategory(fixture_id, category):
            await _step_payload(query, app_context, fixture_id, category)
        case HomeScore(fixture_id, value):
            await _step_away_score(query, app_context, fixture_id, value)
        case ExactScore(fixture_id, home, away):
            await _finalize(
                query, app_context, update, fixture_id, ExactScorePayload(home=home, away=away)
            )
        case WinnerInput(fixture_id, sel):
            await _finalize(query, app_context, update, fixture_id, WinnerPayload(sel=sel))
        case BttsInput(fixture_id, sel):
            await _finalize(query, app_context, update, fixture_id, BttsPayload(sel=sel))
        case OverUnderInput(fixture_id, sel):
            await _finalize(query, app_context, update, fixture_id, OverUnderPayload(sel=sel))
        case FirstTeamInput(fixture_id, sel):
            await _finalize(query, app_context, update, fixture_id, FirstTeamPayload(sel=sel))
        case DeleteBet(bet_id):
            await _delete_bet(query, app_context, user.id, bet_id)


async def _guard_open(query: CallbackQuery, session: Session, fixture_id: int) -> Game | None:
    """Return the open game, or edit a rejection message and return None."""
    game = GameRepository(session).get(fixture_id)
    if game is None:
        await _edit(query, _NOT_FOUND_MESSAGE)
        return None
    if not _is_open(game):
        await _edit(query, _CLOSED_MESSAGE)
        return None
    return game


async def _step_category(
    query: CallbackQuery, app_context: AppContext, telegram_id: int, fixture_id: int
) -> None:
    with app_context.session_factory() as session:
        game = await _guard_open(query, session, fixture_id)
        if game is None:
            return
        text = _category_prompt(session, telegram_id, game)
    await _edit(query, text, keyboard=category_keyboard(fixture_id))


async def _step_payload(
    query: CallbackQuery, app_context: AppContext, fixture_id: int, category: BetCategory
) -> None:
    with app_context.session_factory() as session:
        game = await _guard_open(query, session, fixture_id)
        if game is None:
            return
        if category is BetCategory.EXACT_SCORE:
            await _edit(
                query,
                f"⚽ Quantos gols o <b>{escape(game.home_team_name)}</b> faz?",
                keyboard=home_score_keyboard(fixture_id),
            )
        elif category is BetCategory.WINNER:
            await _edit(
                query,
                "🏆 Quem vence?",
                keyboard=winner_keyboard(
                    fixture_id, game.stage, game.home_team_name, game.away_team_name
                ),
            )
        elif category is BetCategory.BTTS:
            await _edit(query, "🥅 Ambas as equipes marcam?", keyboard=btts_keyboard(fixture_id))
        elif category is BetCategory.OVER_UNDER:
            await _edit(query, "🔢 Total de gols (2.5)?", keyboard=over_under_keyboard(fixture_id))
        else:  # FIRST_TEAM
            await _edit(
                query,
                "👟 Qual equipe marca o primeiro gol?",
                keyboard=first_team_keyboard(fixture_id, game.home_team_name, game.away_team_name),
            )


async def _step_away_score(
    query: CallbackQuery, app_context: AppContext, fixture_id: int, home: int
) -> None:
    with app_context.session_factory() as session:
        game = await _guard_open(query, session, fixture_id)
        if game is None:
            return
        away_name = escape(game.away_team_name)
    await _edit(
        query,
        f"⚽ Placar: <b>{home}</b> x ? — quantos gols o <b>{away_name}</b> faz?",
        keyboard=away_score_keyboard(fixture_id, home),
    )


async def _finalize(
    query: CallbackQuery,
    app_context: AppContext,
    update: Update,
    fixture_id: int,
    payload: Payload,
) -> None:
    with app_context.session_factory() as session:
        game = await _guard_open(query, session, fixture_id)
        if game is None:
            return
        user = update.effective_user
        telegram_id = user.id if user is not None else 0
        PlayerRepository(session).get_or_create(telegram_id, _display_name(update))
        BetRepository(session).upsert(
            fixture_id=fixture_id,
            player_telegram_id=telegram_id,
            category=payload.CATEGORY.value,
            payload_json=serialize_payload(payload),
        )
        description = describe_bet(
            payload, home_team=game.home_team_name, away_team=game.away_team_name
        )
        session.commit()
    await _edit(
        query,
        f"✅ Palpite salvo!\n<b>{escape(description)}</b>\n\nQuer palpitar em outra categoria?",
        keyboard=category_keyboard(fixture_id),
    )


async def _delete_bet(
    query: CallbackQuery, app_context: AppContext, telegram_id: int, bet_id: int
) -> None:
    with app_context.session_factory() as session:
        bets = BetRepository(session)
        bet = bets.get_by_id(bet_id)
        if bet is None or bet.player_telegram_id != telegram_id:
            await _edit(query, "Palpite não encontrado.")
            return
        game = GameRepository(session).get(bet.fixture_id)
        if game is not None and not _is_open(game):
            await _edit(query, _CLOSED_MESSAGE)
            return
        bets.delete(bet_id)
        session.commit()
    await _edit(query, "🗑 Palpite apagado.")


# --- listing commands -----------------------------------------------------------------------


async def minhas_apostas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/minhas_apostas (DM) — open vs settled, with 🗑 delete on open bets (§8.2)."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    app_context = get_app_context(context.application)
    open_lines: list[str] = []
    settled_lines: list[str] = []
    open_buttons: list[tuple[int, str]] = []
    with app_context.session_factory() as session:
        games_repo = GameRepository(session)
        for bet in BetRepository(session).list_for_player(user.id):
            game = games_repo.get(bet.fixture_id)
            if game is None:
                continue
            description = _describe_stored(bet, game)
            if _is_open(game):
                open_lines.append(f"• {escape(_game_label(game))}: {description}")
                open_buttons.append((bet.id, f"{_game_label(game)} — {description}"))
            else:
                mark = "✓" if bet.is_correct else "✗"
                points = bet.points_awarded if bet.points_awarded is not None else 0
                settled_lines.append(
                    f"• {escape(_game_label(game))}: {description} — {mark} ({points} pts)"
                )

    if not open_lines and not settled_lines:
        await message.reply_text("Você ainda não fez nenhum palpite. Use /apostar! 🐯")
        return
    parts: list[str] = []
    if open_lines:
        parts.append("<b>Em aberto</b>\n" + "\n".join(open_lines))
    if settled_lines:
        parts.append("<b>Encerrados</b>\n" + "\n".join(settled_lines))
    keyboard = my_bets_keyboard(open_buttons) if open_buttons else None
    await message.reply_text("\n\n".join(parts), parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def jogos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/jogos — upcoming games + (in DM) the caller's per-category bet status (§8.2)."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None:
        return
    app_context = get_app_context(context.application)
    is_private = chat.type == ChatType.PRIVATE
    viewer_id = user.id if (is_private and user is not None) else None
    with app_context.session_factory() as session:
        games = GameRepository(session).list_upcoming(utcnow())
        if not games:
            await message.reply_text("Não há jogos abertos para apostas no momento.")
            return
        lines = [_jogos_line(session, game, viewer_id) for game in games]
        announce_items = [(g.fixture_id, _game_label(g)) for g in games]
    text = "🐯 <b>Próximos jogos</b>\n\n" + "\n\n".join(lines)
    keyboard = (
        None
        if is_private
        else announcement_keyboard(announce_items, app_context.settings.bot_username)
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


def _jogos_line(session: Session, game: Game, telegram_id: int | None) -> str:
    line = f"• <b>{escape(_game_label(game))}</b> — {format_kickoff_local(game.kickoff_local)}"
    if telegram_id is None:
        return line
    placed = {
        BetCategory(bet.category)
        for bet in BetRepository(session).list_for_player_and_game(telegram_id, game.fixture_id)
    }
    done = ", ".join(sorted(c.value for c in placed)) if placed else "nenhum ainda"
    return f"{line}\n  Seus palpites: {done}"


def register_bet_handlers(application: AnyApplication) -> None:
    """Register the wizard callbacks and the listing commands (deep-link /start lives in app.py)."""
    application.add_handler(CommandHandler("apostar", apostar_handler))
    application.add_handler(CommandHandler("minhas_apostas", minhas_apostas_handler))
    application.add_handler(CommandHandler("jogos", jogos_handler))
    application.add_handler(CallbackQueryHandler(on_callback))
