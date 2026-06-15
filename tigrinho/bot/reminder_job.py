"""Pre-game betting reminders (COMPLETION.md §9.3).

A ``JobQueue.run_repeating`` job. Each sweep posts ONE group reminder for the soonest
unreminded kickoff slot due within ``reminder_lead_minutes`` of now — combining games that
share that exact kickoff time. Pure DB read + group post (no provider calls,
budget-independent). One bad cycle never kills the bot (§14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.keyboards import announcement_keyboard
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import Game, utcnow
from tigrinho.db.repositories import GameRepository
from tigrinho.domain.text_pt import escape, reminder_text
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.reminder_job")

REMINDER_JOB_NAME = "pre_game_reminder"


@dataclass(frozen=True, slots=True)
class _GameView:
    """Plain snapshot of a game for message building (decoupled from the session)."""

    fixture_id: int
    home_team_name: str
    away_team_name: str
    kickoff_local: datetime


def _view(game: Game) -> _GameView:
    return _GameView(
        fixture_id=game.fixture_id,
        home_team_name=game.home_team_name,
        away_team_name=game.away_team_name,
        kickoff_local=game.kickoff_local,
    )


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reminder sweep callback (§9.3). One bad cycle must not kill the bot (§14)."""
    app_context = get_app_context(context.application)
    try:
        await _run_reminder(app_context, context)
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("reminder_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Lembrete falhou: <code>{escape(str(exc))}</code>",
        )


async def _run_reminder(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()
    with app_context.session_factory() as session:
        games = GameRepository(session).list_due_for_reminder(now, settings.reminder_lead)
        views = [_view(g) for g in games]
    if not views:
        return

    text = reminder_text([(v.home_team_name, v.away_team_name, v.kickoff_local) for v in views])
    keyboard = announcement_keyboard(
        [(v.fixture_id, f"{v.home_team_name} x {v.away_team_name}") for v in views],
        settings.bot_username,
    )
    try:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramError as exc:
        _log.error("reminder_send_failed", error=str(exc), count=len(views))
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Falha ao enviar lembrete de {len(views)} jogo(s) (será reenviado): "
            f"<code>{escape(str(exc))}</code>",
        )
        return

    with app_context.session_factory() as session:
        GameRepository(session).mark_reminded([v.fixture_id for v in views], now)
        session.commit()
    _log.info("reminded", count=len(views))


def schedule_reminder_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the reminder sweep every ``reminder_interval_minutes`` (§9.3)."""
    job_queue.run_repeating(
        reminder_job,
        interval=settings.reminder_interval_minutes * 60,
        first=20,
        name=REMINDER_JOB_NAME,
    )
