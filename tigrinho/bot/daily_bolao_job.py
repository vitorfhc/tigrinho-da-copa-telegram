"""Daily AI-curated bolãozinho job (COMPLETION.md §24).

A ``JobQueue.run_daily`` job at ``daily_bolao_time`` (default 18:00 local, the evening before). It
picks the best ≤2 of tomorrow's fixtures via the Gemini scorer and auto-opens a bolãozinho over
them — posting to the group + DMing players exactly like ``/bolaozinho_abrir``. There is no
fallback: any failure DMs the admin and creates nothing. One bad cycle never kills the bot (§14).
The job is only scheduled when ``daily_bolao_enabled`` (see ``app.py``); the ``game_scorer is None``
guard is defensive.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.runtime import get_app_context
from tigrinho.bot.tournament_handlers import announce_open
from tigrinho.config import Settings
from tigrinho.daily_bolao_service import create_daily_bolao
from tigrinho.db.models import utcnow
from tigrinho.domain.text_pt import escape
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.daily_bolao_job")

DAILY_BOLAO_JOB_NAME = "daily_bolao"


async def daily_bolao_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create + auto-open tomorrow's AI bolãozinho (§24). One bad cycle must not kill the bot."""
    app_context = get_app_context(context.application)
    scorer = app_context.game_scorer
    if scorer is None:
        _log.info("daily_bolao_skipped", reason="no game scorer configured")
        return

    settings = app_context.settings
    target_date = (datetime.now(settings.tzinfo) + timedelta(days=1)).date()
    try:
        result = await create_daily_bolao(
            app_context.session_factory,
            scorer,
            settings,
            now=utcnow(),
            target_date=target_date,
        )
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("daily_bolao_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Bolãozinho diário falhou: <code>{escape(str(exc))}</code>",
        )
        return

    if result.status == "created" and result.tournament is not None:
        await announce_open(
            context, settings, result.tournament, list(result.games), list(result.mentions)
        )
        _log.info("daily_bolao_created", tournament_id=result.tournament.id)
    else:
        _log.info("daily_bolao_skipped", reason=result.reason)


def schedule_daily_bolao_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the daily bolãozinho creation at ``daily_bolao_time`` in the timezone (§24)."""
    run_time = settings.daily_bolao_time_obj.replace(tzinfo=settings.tzinfo)
    job_queue.run_daily(daily_bolao_job, time=run_time, name=DAILY_BOLAO_JOB_NAME)
