"""The /palpite command — pick an eligible game, then see its AI palpite (COMPLETION.md §20).

`/palpite` lists the palpite-eligible games — kicking off in the next 24h **and** currently in
progress (LIVE) — as inline buttons (in the group or DM where it is invoked); tapping one shows that
game's AI palpite. The daily 06h job
(:mod:`tigrinho.bot.palpite_job`) pre-computes the day's palpites, so a tap is normally an instant
cache read; if today's cache is cold for the chosen game (e.g. the key was just added, or a new game
entered the window), it generates the day's batch once on demand and caches it — so a day's
predictions are still computed at most once. When no Gemini key is configured, it explains that.
"""

from __future__ import annotations

from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tigrinho.bot.callbacks import PalpiteView, decode
from tigrinho.bot.keyboards import palpite_games_keyboard
from tigrinho.bot.messaging import safe_edit_text
from tigrinho.bot.runtime import AnyApplication, get_app_context
from tigrinho.db.models import Game, GameStatus, utcnow
from tigrinho.db.repositories import GameRepository
from tigrinho.domain.text_pt import (
    format_kickoff_short,
    palpite_error_text,
    palpite_generating_text,
    palpite_no_games_text,
    palpite_no_key_text,
    palpite_pick_text,
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


def _picker_label(game: Game) -> str:
    """Compact picker-button label, e.g. ``Brasil x Argentina · 16/06 16:00``.

    A running (LIVE) game is marked ``🔴 … · ao vivo`` instead of its (already-past) kickoff time.
    """
    teams = f"{game.home_team_name} x {game.away_team_name}"
    if game.status is GameStatus.LIVE:
        return f"🔴 {teams} · ao vivo"
    return f"{teams} · {format_kickoff_short(game.kickoff_local)}"


def _render(item: RenderablePalpite) -> str:
    return palpite_text(
        home=item.home_team,
        away=item.away_team,
        kickoff_local=item.kickoff_local,
        analysis=item.palpite.analysis,
        payloads=item.palpite.payloads(),
        curiosity=item.palpite.curiosity,
    )


async def palpite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/palpite — list the eligible games (next-24h + live); tapping one shows its palpite (§20)."""
    message = update.effective_message
    if message is None:
        return
    app_context = get_app_context(context.application)
    if app_context.palpite_generator is None:
        await message.reply_text(palpite_no_key_text(), parse_mode=ParseMode.HTML)
        return

    now = utcnow()
    with app_context.session_factory() as session:
        games = GameRepository(session).list_palpite_games(
            now, PALPITE_HORIZON, app_context.settings.match_window_hours
        )
        items = [(g.fixture_id, _picker_label(g)) for g in games]
    if not items:
        await message.reply_text(palpite_no_games_text(), parse_mode=ParseMode.HTML)
        return
    await message.reply_text(
        palpite_pick_text(), parse_mode=ParseMode.HTML, reply_markup=palpite_games_keyboard(items)
    )


async def palpite_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline callback (^pv:) — show the chosen game's AI palpite, generating on demand (§20)."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, PalpiteView):
        return
    await query.answer()

    app_context = get_app_context(context.application)
    generator = app_context.palpite_generator
    if generator is None:
        await safe_edit_text(query, palpite_no_key_text())
        return

    now = utcnow()
    palpite_date = datetime.now(app_context.settings.tzinfo).date()
    live_window = app_context.settings.match_window_hours
    fixture_id = data.fixture_id

    with app_context.session_factory() as session:
        candidate_ids = {
            g.fixture_id
            for g in GameRepository(session).list_palpite_games(now, PALPITE_HORIZON, live_window)
        }

    def _missing_unattempted(rendered: list[RenderablePalpite]) -> set[int]:
        # Eligible fixtures with no cached palpite we have not already tried today — a fixture the
        # model keeps omitting stays "attempted" so it won't re-trigger generation forever (§20.1).
        attempted = {fid for (d, fid) in app_context.palpite_attempted if d == palpite_date}
        return candidate_ids - {r.fixture_id for r in rendered} - attempted

    rendered = load_today_palpites(
        app_context.session_factory,
        now=now,
        palpite_date=palpite_date,
        live_window_hours=live_window,
    )
    if fixture_id in _missing_unattempted(rendered):
        # Cold cache for the chosen game. Single-flight: if a generation is already running, don't
        # fire a second Gemini request — tell the caller to retry shortly.
        if app_context.palpite_lock.locked():
            await safe_edit_text(query, palpite_generating_text())
            return
        async with app_context.palpite_lock:
            # Re-read under the lock: a generation that finished while we waited may have warmed it.
            rendered = load_today_palpites(
                app_context.session_factory,
                now=now,
                palpite_date=palpite_date,
                live_window_hours=live_window,
            )
            to_generate = _missing_unattempted(rendered)
            if fixture_id in to_generate:
                # We are the single generator and the chosen game is still cold. This is slow (a
                # grounded "think hard" call that fills the whole day's batch), so tell the user.
                await safe_edit_text(query, palpite_working_text())
                try:
                    await generate_palpites(
                        app_context.session_factory,
                        generator,
                        now=now,
                        palpite_date=palpite_date,
                        live_window_hours=live_window,
                    )
                except Exception as exc:  # noqa: BLE001 - friendly error, never crash the bot
                    _log.error(
                        "palpite_generation_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    await safe_edit_text(query, palpite_error_text())
                    return
                # Mark every fixture we just asked for as attempted today — even ones the model
                # omitted — so an incomplete batch doesn't re-run generation on the next tap.
                app_context.palpite_attempted.update((palpite_date, fid) for fid in to_generate)
                rendered = load_today_palpites(
                    app_context.session_factory,
                    now=now,
                    palpite_date=palpite_date,
                    live_window_hours=live_window,
                )

    selected = next((r for r in rendered if r.fixture_id == fixture_id), None)
    if selected is None:
        await safe_edit_text(query, palpite_error_text())
        return
    await safe_edit_text(query, _render(selected))


def register_palpite_handlers(application: AnyApplication) -> None:
    """Register /palpite and its game-selection callback (^pv: precedes the wizard's catch-all)."""
    application.add_handler(CommandHandler("palpite", palpite_handler))
    application.add_handler(CallbackQueryHandler(palpite_select, pattern="^pv:"))
