"""Scoreboard command + Geral↔Semana toggle (COMPLETION.md §10).

`/placar` posts the standings (default **Geral**) with an inline toggle that **edits the same
message** to switch to **Semana** and back; it also accepts ``/placar semana``. The standings are
computed purely from settled bets via :mod:`tigrinho.scoreboard`, so the CLI rebuilds them the same.
"""

from __future__ import annotations

from datetime import datetime

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tigrinho.board_data import load_board_records, load_game_records, load_games_records
from tigrinho.bot.callbacks import (
    BoardScope,
    BoardView,
    GameBoard,
    GamesBoardCompute,
    GamesBoardToggle,
    decode,
)
from tigrinho.bot.keyboards import (
    board_toggle_keyboard,
    combined_games_keyboard,
    ended_games_keyboard,
)
from tigrinho.bot.messaging import safe_edit_text
from tigrinho.bot.runtime import AnyApplication, AppContext, get_app_context
from tigrinho.db.repositories import GameRepository
from tigrinho.domain.text_pt import board_text, game_board_text, games_board_text
from tigrinho.scoreboard import rank

_TOP_N = 15
# How many recently-ended games to offer in the /placar_jogo picker (inline-button budget).
_ENDED_GAMES_LIMIT = 15
# How many recently-ended games to offer in the /placar_jogos multi-select picker.
_COMBINED_GAMES_LIMIT = 10
_COMBINED_PICKER_PROMPT = "Escolha os jogos para somar o placar (toque para marcar):"


def _render(
    app_context: AppContext, scope: BoardScope, caller_id: int | None
) -> tuple[str, InlineKeyboardMarkup]:
    weekly = scope == "semana"
    now_local = datetime.now(app_context.settings.tzinfo).replace(tzinfo=None)
    with app_context.session_factory() as session:
        entries = rank(load_board_records(session, weekly=weekly, now_local=now_local))
    rows = [(e.rank, e.display_name, e.points) for e in entries[:_TOP_N]]
    caller_outside = None
    if caller_id is not None:
        caller = next((e for e in entries if e.telegram_id == caller_id), None)
        if caller is not None and caller.rank > _TOP_N:
            caller_outside = (caller.rank, caller.points)
    text = board_text(weekly=weekly, rows=rows, caller_outside=caller_outside)
    return text, board_toggle_keyboard(weekly)


async def placar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/placar [semana] — post the scoreboard (default Geral)."""
    message = update.effective_message
    if message is None:
        return
    app_context = get_app_context(context.application)
    args = context.args or []
    scope: BoardScope = "semana" if args and args[0].lower().startswith("sem") else "geral"
    user = update.effective_user
    text, keyboard = _render(app_context, scope, user.id if user is not None else None)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def board_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline toggle — edits the same message to switch Geral↔Semana (§10)."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, BoardView):
        return
    await query.answer()
    app_context = get_app_context(context.application)
    user = update.effective_user
    text, keyboard = _render(app_context, data.scope, user.id if user is not None else None)
    await safe_edit_text(query, text, reply_markup=keyboard)


def _ended_game_label(home: str, away: str, home_goals: int | None, away_goals: int | None) -> str:
    """Short button/list label for an ended game, e.g. ``Brasil 2 x 1 Argentina``."""
    if home_goals is not None and away_goals is not None:
        return f"{home} {home_goals} x {away_goals} {away}"
    return f"{home} x {away}"


async def placar_jogo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/placar_jogo — list recently-ended games; tapping one shows its per-game scoreboard (§10)."""
    message = update.effective_message
    if message is None:
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        games = GameRepository(session).list_recently_ended(_ENDED_GAMES_LIMIT)
        items = [
            (
                g.fixture_id,
                _ended_game_label(
                    g.home_team_name, g.away_team_name, g.home_goals_90, g.away_goals_90
                ),
            )
            for g in games
        ]
    if not items:
        await message.reply_text("Nenhum jogo encerrado ainda. 🐯")
        return
    await message.reply_text(
        "Escolha um jogo encerrado para ver o placar:",
        reply_markup=ended_games_keyboard(items),
    )


def _render_game_board(app_context: AppContext, fixture_id: int) -> str | None:
    """Build the per-game scoreboard text, or None if the game is unknown/not finished."""
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None or game.settled_at is None:
            return None
        entries = rank(load_game_records(session, fixture_id))
        rows = [(e.rank, e.display_name, e.points) for e in entries]
        return game_board_text(
            home=game.home_team_name,
            away=game.away_team_name,
            home_goals=game.home_goals_90,
            away_goals=game.away_goals_90,
            rows=rows,
        )


async def game_board_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline callback (^gb:) — render the chosen game's scoreboard, editing the picker message."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, GameBoard):
        return
    await query.answer()
    text = _render_game_board(get_app_context(context.application), data.fixture_id)
    if text is None:
        await safe_edit_text(query, "Jogo não encontrado ou ainda não encerrado.")
        return
    await safe_edit_text(query, text)


def _render_picker(app_context: AppContext, mask: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Build the /placar_jogos multi-select picker for ``mask``; None when no game has ended."""
    with app_context.session_factory() as session:
        games = GameRepository(session).list_recently_ended(_COMBINED_GAMES_LIMIT)
        labels = [
            _ended_game_label(g.home_team_name, g.away_team_name, g.home_goals_90, g.away_goals_90)
            for g in games
        ]
    if not labels:
        return None
    return _COMBINED_PICKER_PROMPT, combined_games_keyboard(labels, mask)


def _render_combined_board(app_context: AppContext, mask: int) -> str | None:
    """Combined-board text for the games selected by ``mask``; None if nothing is selected."""
    with app_context.session_factory() as session:
        games = GameRepository(session).list_recently_ended(_COMBINED_GAMES_LIMIT)
        selected = [g for i, g in enumerate(games) if mask & (1 << i)]
        if not selected:
            return None
        records = load_games_records(session, [g.fixture_id for g in selected])
        rows = [(e.rank, e.display_name, e.points) for e in rank(records)]
        header_games = [
            (g.home_team_name, g.away_team_name, g.home_goals_90, g.away_goals_90) for g in selected
        ]
        return games_board_text(games=header_games, rows=rows)


async def placar_jogos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/placar_jogos — multi-select picker; sums points across the chosen ended games (§10)."""
    message = update.effective_message
    if message is None:
        return
    rendered = _render_picker(get_app_context(context.application), mask=0)
    if rendered is None:
        await message.reply_text("Nenhum jogo encerrado ainda. 🐯")
        return
    text, keyboard = rendered
    await message.reply_text(text, reply_markup=keyboard)


async def games_board_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline (^pjt:) — flip one game's selection bit and re-render the picker."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, GamesBoardToggle):
        return
    await query.answer()
    new_mask = data.mask ^ (1 << data.index)
    rendered = _render_picker(get_app_context(context.application), new_mask)
    if rendered is None:
        await safe_edit_text(query, "Nenhum jogo encerrado ainda. 🐯")
        return
    text, keyboard = rendered
    await safe_edit_text(query, text, reply_markup=keyboard)


async def games_board_compute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline (^pjc:) — render the combined board for the selected games (toast if none)."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, GamesBoardCompute):
        return
    text = _render_combined_board(get_app_context(context.application), data.mask)
    if text is None:
        await query.answer("Selecione ao menos um jogo.")
        return
    await query.answer()
    await safe_edit_text(query, text)


def register_board_handlers(application: AnyApplication) -> None:
    """Register /placar + /placar_jogo + /placar_jogos and their callbacks (precede catch-all)."""
    application.add_handler(CommandHandler("placar", placar_handler))
    application.add_handler(CommandHandler("placar_jogo", placar_jogo_handler))
    application.add_handler(CommandHandler("placar_jogos", placar_jogos_handler))
    application.add_handler(CallbackQueryHandler(board_toggle, pattern="^bv:"))
    application.add_handler(CallbackQueryHandler(game_board_select, pattern="^gb:"))
    application.add_handler(CallbackQueryHandler(games_board_toggle, pattern="^pjt:"))
    application.add_handler(CallbackQueryHandler(games_board_compute, pattern="^pjc:"))
