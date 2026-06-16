"""Daily AI palpite generation job (COMPLETION.md §20).

A ``JobQueue.run_daily`` job at ``palpite_time`` (default 06:00 local). It runs the grounded
Gemini analysis once for the games kicking off in the next 24h and caches the validated result,
so ``/palpite`` is a fast cache read for the rest of the day. The job only **warms the cache** —
it does not post to the group (that is the user-invoked ``/palpite``, §20). When no Gemini key is
configured the job is a no-op. One bad cycle never kills the bot (§14).
"""

from __future__ import annotations

from datetime import datetime

from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.runtime import get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import utcnow
from tigrinho.domain.text_pt import escape
from tigrinho.logging import get_logger
from tigrinho.palpite_service import generate_palpites

_log = get_logger("tigrinho.palpite_job")

PALPITE_JOB_NAME = "daily_palpite"


async def palpite_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily AI palpite generation (§20). One bad cycle must not kill the bot (§14)."""
    app_context = get_app_context(context.application)
    generator = app_context.palpite_generator
    if generator is None:
        _log.info("palpite_job_skipped", reason="no gemini key configured")
        return

    settings = app_context.settings
    now = utcnow()
    palpite_date = datetime.now(settings.tzinfo).date()
    try:
        generated = await generate_palpites(
            app_context.session_factory, generator, now=now, palpite_date=palpite_date
        )
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("palpite_job_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Geração de palpites da IA falhou: <code>{escape(str(exc))}</code>",
        )
        return
    _log.info("palpite_job_done", generated=len(generated))


def schedule_palpite_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the daily AI palpite generation at ``palpite_time`` in the timezone (§20)."""
    run_time = settings.palpite_time_obj.replace(tzinfo=settings.tzinfo)
    job_queue.run_daily(palpite_job, time=run_time, name=PALPITE_JOB_NAME)
