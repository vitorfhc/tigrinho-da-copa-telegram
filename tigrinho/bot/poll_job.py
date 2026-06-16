"""Live polling + auto-settlement (COMPLETION.md §9.2, §7.3).

A ``JobQueue.run_repeating`` job. It returns **without any API call** when no game is in its live
window. Otherwise, honoring the budget priority *settlement > polling* (§7.3):

1. **Settlement first** — for each active game already past ``kickoff + SETTLE_AFTER`` (i.e. 90′ +
   stoppage should be over) it fetches ``get_match_result()`` once (budgeted) and settles if the
   provider reports it FINISHED. This does **not** depend on the game still being in the live feed,
   so a game that finished and dropped out of ``get_live_results()`` between polls is still settled.
2. **Live polling (lower priority)** — for games still in progress it makes **one**
   ``get_live_results()`` call to update live status and catch early finishes.

Games still unsettled past ``kickoff + match_window_hours`` trigger an admin DM (manual CLI
settlement). The cap hard-stops further provider calls and DMs the admin once per budget day (§7.3).
"""

from __future__ import annotations

from datetime import timedelta

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import alert_cap_reached, notify_admin
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import GameStatus, utcnow
from tigrinho.db.repositories import GameRepository
from tigrinho.domain.text_pt import CATEGORY_LABELS, escape, results_text
from tigrinho.logging import get_logger
from tigrinho.providers.budget import BudgetExceeded
from tigrinho.settlement_service import settle_fixture

_log = get_logger("tigrinho.poll_job")

POLL_JOB_NAME = "live_poll"
# A 90′ game (incl. half-time + stoppage) is over well within 2h of kickoff; past this we settle
# proactively via get_match_result rather than relying on the live feed (§9.2).
SETTLE_AFTER = timedelta(hours=2)


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live-poll active games and auto-settle finished ones (§9.2). Never kills the bot (§14)."""
    app_context = get_app_context(context.application)
    try:
        await _run_poll(app_context, context)
    except BudgetExceeded:
        await alert_cap_reached(app_context, context.bot, app_context.budget.today())
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("poll_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Polling falhou: <code>{escape(str(exc))}</code>",
        )


async def _run_poll(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()

    with app_context.session_factory() as session:
        games = GameRepository(session)
        active = [
            (g.fixture_id, g.kickoff_utc)
            for g in games.list_active(now, settings.match_window_hours)
        ]
        stuck = [
            (g.fixture_id, f"{g.home_team_name} x {g.away_team_name}")
            for g in games.list_stuck(now, settings.match_window_hours)
        ]

    # Alert the admin once per stuck game, not every cycle. Prune ids that are no longer stuck
    # (settled or rescheduled) so a game that gets stuck again later can re-alert.
    app_context.stuck_alerted.intersection_update(fixture_id for fixture_id, _ in stuck)
    for fixture_id, label in stuck:
        if fixture_id in app_context.stuck_alerted:
            continue
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⏳ Jogo {label} (#{fixture_id}) segue sem resultado após a janela. "
            "Pode precisar de settle manual via CLI.",
        )
        app_context.stuck_alerted.add(fixture_id)

    if not active:
        return  # NO API call when nothing is active (§9.2)

    due = [fid for fid, kickoff in active if now >= kickoff + SETTLE_AFTER]
    in_progress = [fid for fid, kickoff in active if now < kickoff + SETTLE_AFTER]

    # (1) Settlement priority (§7.3): settle overdue games first, regardless of the live feed.
    for fixture_id in due:
        await _settle_and_announce(app_context, context, fixture_id)

    if not in_progress:
        return

    # (2) Lower-priority live poll for games still in progress (catches early finishes).
    live = await app_context.budget.guarded(app_context.provider.get_live_results)
    live_by_id = {result.fixture_id: result for result in live}
    finished: list[int] = []
    with app_context.session_factory() as session:
        games = GameRepository(session)
        for fixture_id in in_progress:
            game = games.get(fixture_id)
            result = live_by_id.get(fixture_id)
            if game is None or result is None:
                continue
            if result.status is GameStatus.FINISHED:
                finished.append(fixture_id)
            elif result.status is GameStatus.LIVE and game.status is not GameStatus.LIVE:
                game.status = GameStatus.LIVE
        session.commit()

    for fixture_id in finished:
        await _settle_and_announce(app_context, context, fixture_id)


async def _settle_and_announce(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, fixture_id: int
) -> None:
    # Skip the budgeted provider call entirely if the game is already settled (§9.2 "if needed").
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None or game.settled_at is not None:
            return

    result = await app_context.budget.guarded(
        lambda: app_context.provider.get_match_result(fixture_id)
    )

    if result.status is not GameStatus.FINISHED:
        # Not over yet (e.g. extra time): record LIVE and retry on a later cycle.
        with app_context.session_factory() as session:
            game = GameRepository(session).get(fixture_id)
            if (
                game is not None
                and game.settled_at is None
                and result.status is GameStatus.LIVE
                and game.status is not GameStatus.LIVE
            ):
                game.status = GameStatus.LIVE
                session.commit()
        return

    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None or game.settled_at is not None:
            return  # settled in between (idempotent guard) — don't double-post
        summary = settle_fixture(session, game, result)
        players = [
            (
                player.telegram_id,
                player.display_name,
                player.total_points,
                [(CATEGORY_LABELS[c.category], c.is_correct, c.points) for c in player.categories],
            )
            for player in summary.players
        ]
        text = results_text(
            home=summary.home_team_name,
            away=summary.away_team_name,
            home_goals=summary.home_goals_90,
            away_goals=summary.away_goals_90,
            first_team_name=summary.first_scoring_team_name,
            players=players,
        )
        session.commit()

    # Grades are committed; the group post is best-effort but must not be lost silently (§14).
    try:
        await context.bot.send_message(
            chat_id=app_context.settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
        )
        _log.info("settled", fixture_id=fixture_id, players=len(summary.players))
    except TelegramError as exc:
        _log.error("results_post_failed", fixture_id=fixture_id, error=str(exc))
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Resultado do jogo #{fixture_id} apurado, mas falhou ao postar no grupo: "
            f"<code>{escape(str(exc))}</code>",
        )


def schedule_poll_job(job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings) -> None:
    """Schedule live polling every ``poll_interval_minutes`` (§9.2)."""
    job_queue.run_repeating(
        poll_job,
        interval=settings.poll_interval_minutes * 60,
        first=10,
        name=POLL_JOB_NAME,
    )
