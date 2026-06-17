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
from tigrinho.domain.live import Side, goal_progression
from tigrinho.domain.text_pt import (
    CATEGORY_LABELS,
    cancellation_reason_pt,
    escape,
    goal_cancelled_text,
    goal_text,
    kickoff_text,
    results_text,
)
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

    # (2) Lower-priority live poll for in-progress games: status, kickoff + goal posts (§9.4),
    #     and early finishes.
    live = await app_context.budget.guarded(app_context.provider.get_live_results)
    live_by_id = {result.fixture_id: result for result in live}
    finished: list[int] = []
    kickoffs: list[tuple[str, str]] = []
    goal_fixtures: list[int] = []
    cancel_fixtures: list[tuple[int, int, int]] = []  # (fixture_id, live_home, live_away)
    with app_context.session_factory() as session:
        games = GameRepository(session)
        for fixture_id in in_progress:
            game = games.get(fixture_id)
            result = live_by_id.get(fixture_id)
            if game is None or result is None:
                continue
            if result.status is GameStatus.FINISHED:
                finished.append(fixture_id)
                continue
            if result.status is not GameStatus.LIVE:
                continue
            if game.status is not GameStatus.LIVE:
                game.status = GameStatus.LIVE
            if game.started_at is None:
                game.started_at = now  # kickoff detected this cycle
                kickoffs.append((game.home_team_name, game.away_team_name))
            # Goals only after kickoff; same-cycle catch-up is fine (started_at just set above).
            live_home = result.live_home_goals or 0
            live_away = result.live_away_goals or 0
            live_total = live_home + live_away
            if live_total > game.goals_announced:
                goal_fixtures.append(fixture_id)
            elif live_total < game.goals_announced:
                # Running total dropped → a counted goal was disallowed by VAR (§9.4). The cursor
                # is resynced in the handler, after the retraction posts (so a failed send retries).
                cancel_fixtures.append((fixture_id, live_home, live_away))
        session.commit()

    for home, away in kickoffs:
        text = kickoff_text(home, away)
        await _post_to_group(app_context, context, text, what="o início do jogo")

    for fixture_id in goal_fixtures:
        await _announce_goals(app_context, context, fixture_id)

    for fixture_id, live_home, live_away in cancel_fixtures:
        await _announce_cancellations(
            app_context, context, fixture_id, live_home=live_home, live_away=live_away
        )

    for fixture_id in finished:
        await _settle_and_announce(app_context, context, fixture_id)


async def _post_to_group(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, text: str, *, what: str
) -> None:
    """Best-effort live group post; on failure log + DM admin, never crash the bot (§14)."""
    try:
        await context.bot.send_message(
            chat_id=app_context.settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except TelegramError as exc:
        _log.error("group_post_failed", what=what, error=str(exc))
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Falha ao postar {what} no grupo: <code>{escape(str(exc))}</code>",
        )


async def _announce_goals(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, fixture_id: int
) -> None:
    """Fetch the goal timeline and post one message per *new* goal (§9.4)."""
    events = await app_context.budget.guarded(
        lambda: app_context.provider.get_goal_events(fixture_id)
    )
    messages: list[str] = []
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None:
            return
        progression = goal_progression(game.home_team_id, game.away_team_id, events)
        for prog in progression[game.goals_announced :]:
            scoring_team = (
                game.home_team_name if prog.scoring_side is Side.HOME else game.away_team_name
            )
            messages.append(
                goal_text(
                    scoring_team=scoring_team,
                    home_team=game.home_team_name,
                    away_team=game.away_team_name,
                    home_score=prog.home_score,
                    away_score=prog.away_score,
                    minute=prog.goal.minute,
                    extra=prog.goal.extra,
                    scorer=prog.goal.player_name,
                    is_penalty=prog.goal.is_penalty,
                    is_own_goal=prog.goal.is_own_goal,
                )
            )
        game.goals_announced = len(progression)
        session.commit()

    for message in messages:
        await _post_to_group(app_context, context, message, what="um gol")


async def _announce_cancellations(
    app_context: AppContext,
    context: ContextTypes.DEFAULT_TYPE,
    fixture_id: int,
    *,
    live_home: int,
    live_away: int,
) -> None:
    """Post one retraction per goal disallowed by VAR since the last poll, then resync (§9.4).

    The live score (authoritative, VAR-adjusted) tells us *how many* counted goals vanished; the
    ``Var`` events feed enriches each retraction with the team/scorer/minute/reason when available
    (the score often drops a poll or two before the event surfaces, so a generic notice is sent if
    the detail isn't there yet). The cursor is resynced to the live total only after a successful
    fetch, mirroring :func:`_announce_goals` so a failed cycle simply retries.
    """
    cancellations = await app_context.budget.guarded(
        lambda: app_context.provider.get_goal_cancellations(fixture_id)
    )
    live_total = live_home + live_away
    messages: list[str] = []
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None:
            return
        dropped = game.goals_announced - live_total
        if dropped <= 0:
            return  # state moved on between cycles (re-counted) — nothing to retract
        # Pair each vanished goal with the most-recent VAR events; any shortfall stays generic.
        recent = cancellations[-dropped:]
        offset = dropped - len(recent)
        for index in range(dropped):
            var = recent[index - offset] if index >= offset else None
            scoring_team: str | None = None
            scorer: str | None = None
            minute: int | None = None
            extra: int | None = None
            reason: str | None = None
            if var is not None:
                if var.team_id == game.home_team_id:
                    scoring_team = game.home_team_name
                elif var.team_id == game.away_team_id:
                    scoring_team = game.away_team_name
                scorer = var.player_name
                minute = var.minute
                extra = var.extra
                reason = cancellation_reason_pt(var.detail)
            messages.append(
                goal_cancelled_text(
                    scoring_team=scoring_team,
                    home_team=game.home_team_name,
                    away_team=game.away_team_name,
                    home_score=live_home,
                    away_score=live_away,
                    minute=minute,
                    extra=extra,
                    scorer=scorer,
                    reason=reason,
                )
            )
        game.goals_announced = live_total
        session.commit()

    for message in messages:
        await _post_to_group(app_context, context, message, what="um gol anulado")


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
