"""Scoreboard command + Geral↔Semana toggle (COMPLETION.md §10).

`/placar` posts the standings (default **Geral**) with an inline toggle that **edits the same
message** to switch to **Semana** and back; it also accepts ``/placar semana``. The standings are
computed purely from settled bets via :mod:`tigrinho.scoreboard`, so the CLI rebuilds them the same.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tigrinho.bot.callbacks import BoardScope, BoardView, decode
from tigrinho.bot.keyboards import board_toggle_keyboard
from tigrinho.bot.runtime import AnyApplication, AppContext, get_app_context
from tigrinho.db.models import GameStatus
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.domain.bets import BetCategory
from tigrinho.domain.text_pt import board_text
from tigrinho.scoreboard import BetRecord, in_current_week, rank

_TOP_N = 15


def _load_records(session: Session, *, weekly: bool, now_local: datetime) -> list[BetRecord]:
    games = GameRepository(session)
    players = PlayerRepository(session)
    records: list[BetRecord] = []
    for bet in BetRepository(session).list_settled():
        if bet.points_awarded is None:
            continue
        game = games.get(bet.fixture_id)
        if game is None or game.status is GameStatus.VOID:
            continue
        if weekly and not in_current_week(game.kickoff_local, now_local):
            continue
        player = players.get(bet.player_telegram_id)
        if player is None:
            continue
        correct = bool(bet.is_correct)
        records.append(
            BetRecord(
                telegram_id=player.telegram_id,
                display_name=player.display_name,
                created_at=player.created_at,
                points=bet.points_awarded,
                is_correct=correct,
                is_exact_score_hit=correct and bet.category == BetCategory.EXACT_SCORE.value,
            )
        )
    return records


def _render(
    app_context: AppContext, scope: BoardScope, caller_id: int | None
) -> tuple[str, InlineKeyboardMarkup]:
    weekly = scope == "semana"
    now_local = datetime.now(app_context.settings.tzinfo).replace(tzinfo=None)
    with app_context.session_factory() as session:
        entries = rank(_load_records(session, weekly=weekly, now_local=now_local))
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
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


def register_board_handlers(application: AnyApplication) -> None:
    """Register /placar + the toggle (its ^bv: pattern must precede the wizard catch-all)."""
    application.add_handler(CommandHandler("placar", placar_handler))
    application.add_handler(CallbackQueryHandler(board_toggle, pattern="^bv:"))
