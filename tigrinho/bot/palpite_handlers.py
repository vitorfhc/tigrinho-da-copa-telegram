"""The /palpite command — AI palpites for the next 24h (COMPLETION.md §20).

Posts the cached AI palpites for the games kicking off in the next 24h in the chat where it is
invoked (group or DM). The daily 06h job (:mod:`tigrinho.bot.palpite_job`) pre-computes them, so
/palpite is usually an instant cache read; if today's cache is cold (e.g. the key was just added,
or a new game entered the window), it generates once on demand and caches the result — so a day's
predictions are still computed at most once. When no Gemini key is configured, it explains that.
"""

from __future__ import annotations

from datetime import datetime

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

from tigrinho.bot.runtime import AnyApplication, get_app_context
from tigrinho.db.models import utcnow
from tigrinho.db.repositories import GameRepository
from tigrinho.domain.text_pt import (
    palpite_error_text,
    palpite_generating_text,
    palpite_no_games_text,
    palpite_no_key_text,
    palpite_text,
    palpite_working_text,
)
from tigrinho.logging import get_logger
from tigrinho.palpite_service import (
    PALPITE_HORIZON,
    RenderablePalpite,
    generate_palpites,
    load_today_palpites,
)

_log = get_logger("tigrinho.palpite_handlers")


async def _post_palpites(message: Message, rendered: list[RenderablePalpite]) -> None:
    for item in rendered:
        await message.reply_text(
            palpite_text(
                home=item.home_team,
                away=item.away_team,
                kickoff_local=item.kickoff_local,
                analysis=item.palpite.analysis,
                payloads=item.palpite.payloads(),
                curiosity=item.palpite.curiosity,
            ),
            parse_mode=ParseMode.HTML,
        )


async def palpite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/palpite — show the AI palpites for the next 24h (generating on demand if needed)."""
    message = update.effective_message
    if message is None:
        return
    app_context = get_app_context(context.application)
    generator = app_context.palpite_generator
    if generator is None:
        await message.reply_text(palpite_no_key_text(), parse_mode=ParseMode.HTML)
        return

    settings = app_context.settings
    now = utcnow()
    palpite_date = datetime.now(settings.tzinfo).date()

    with app_context.session_factory() as session:
        upcoming_ids = {
            g.fixture_id for g in GameRepository(session).list_upcoming_within(now, PALPITE_HORIZON)
        }
    if not upcoming_ids:
        await message.reply_text(palpite_no_games_text(), parse_mode=ParseMode.HTML)
        return

    def _missing_unattempted(rendered: list[RenderablePalpite]) -> set[int]:
        # Fixtures that have no cached palpite AND we have not already tried to generate today.
        # A fixture the model keeps omitting stays "attempted" so it won't re-trigger forever.
        attempted = {fid for (d, fid) in app_context.palpite_attempted if d == palpite_date}
        return upcoming_ids - {r.fixture_id for r in rendered} - attempted

    rendered = load_today_palpites(app_context.session_factory, now=now, palpite_date=palpite_date)
    if _missing_unattempted(rendered):
        # Cold (or partial) cache. Another /palpite may already be generating — if so, don't fire a
        # second Gemini request; tell the caller to retry shortly.
        if app_context.palpite_lock.locked():
            await message.reply_text(palpite_generating_text(), parse_mode=ParseMode.HTML)
            return
        async with app_context.palpite_lock:
            # Re-read under the lock: a generation that finished while we waited may have warmed it.
            rendered = load_today_palpites(
                app_context.session_factory, now=now, palpite_date=palpite_date
            )
            to_generate = _missing_unattempted(rendered)
            if to_generate:
                # We hold the lock and the cache is still cold: we are the single generator. This is
                # slow (grounded "think hard" call), so tell the user first.
                await message.reply_text(palpite_working_text(), parse_mode=ParseMode.HTML)
                try:
                    await generate_palpites(
                        app_context.session_factory,
                        generator,
                        now=now,
                        palpite_date=palpite_date,
                    )
                except Exception as exc:  # noqa: BLE001 - friendly error, never crash the bot
                    _log.error(
                        "palpite_generation_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    await message.reply_text(palpite_error_text(), parse_mode=ParseMode.HTML)
                    return
                # Mark every fixture we just asked for as attempted today — even ones the model
                # omitted — so an incomplete batch doesn't re-run generation on the next call.
                app_context.palpite_attempted.update((palpite_date, fid) for fid in to_generate)
                rendered = load_today_palpites(
                    app_context.session_factory, now=now, palpite_date=palpite_date
                )

    if not rendered:
        await message.reply_text(palpite_error_text(), parse_mode=ParseMode.HTML)
        return
    await _post_palpites(message, rendered)


def register_palpite_handlers(application: AnyApplication) -> None:
    """Register the /palpite command."""
    application.add_handler(CommandHandler("palpite", palpite_handler))
