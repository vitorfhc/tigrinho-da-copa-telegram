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
from tigrinho.bot.tournament_announce import resolve_and_post
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, utcnow
from tigrinho.db.repositories import BetRepository, GameRepository
from tigrinho.domain.bets import BetCategory, parse_payload
from tigrinho.domain.text_pt import (
    CATEGORY_LABELS,
    closed_bets_text,
    describe_bet_value,
    escape,
    goal_cancelled_text,
    goal_text,
    kickoff_text,
    results_text,
)
from tigrinho.logging import get_logger
from tigrinho.providers.base import MatchResult
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
    # (home, away, reveal_text) — reveal_text is the bet-reveal post (§9.4), or None if nobody bet.
    kickoffs: list[tuple[str, str, str | None]] = []
    goal_messages: list[str] = []
    cancel_messages: list[str] = []
    with app_context.session_factory() as session:
        games = GameRepository(session)
        bet_repo = BetRepository(session)
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
                # Bets close at kickoff (§8.1), so the reveal lists every bet now placed (§9.4).
                reveal = closed_bets_text(
                    home=game.home_team_name,
                    away=game.away_team_name,
                    items=[
                        (
                            BetCategory(bet.category),
                            bet.player.display_name,
                            describe_bet_value(
                                parse_payload(BetCategory(bet.category), bet.payload_json),
                                home_team=game.home_team_name,
                                away_team=game.away_team_name,
                            ),
                        )
                        for bet in bet_repo.list_for_game(fixture_id)
                    ],
                )
                kickoffs.append((game.home_team_name, game.away_team_name, reveal))
            # Goals & VAR retractions come straight from the live score split (§9.4) — no extra
            # /fixtures/events lookup, so each goal posts the moment the score ticks. Same-cycle
            # catch-up is fine (started_at may have just been set above).
            goals, cancels = _diff_live_score(game, result)
            goal_messages.extend(goals)
            cancel_messages.extend(cancels)
        session.commit()

    for home, away, reveal in kickoffs:
        text = kickoff_text(home, away)
        await _post_to_group(app_context, context, text, what="o início do jogo")
        if reveal is not None:
            await _post_to_group(app_context, context, reveal, what="as apostas do jogo")

    for message in goal_messages:
        await _post_to_group(app_context, context, message, what="um gol")

    for message in cancel_messages:
        await _post_to_group(app_context, context, message, what="um gol anulado")

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


def _diff_live_score(game: Game, result: MatchResult) -> tuple[list[str], list[str]]:
    """Diff the live score split against the announced cursor (§9.4).

    Returns ``(goal_posts, cancellation_posts)`` and advances ``game``'s per-side + total goal
    cursor to the live score. The live feed's ``goals.{home,away}`` already credits own goals to the
    correct side, so the team whose tally moved names the scoring team directly — no
    ``/fixtures/events`` lookup, hence each goal posts the moment the score ticks. A side's tally
    rising yields a goal post; falling (a VAR retraction) yields a cancellation post. The away side
    is applied after the home side so a rare multi-goal cycle still shows a sensible running score.
    """
    live_home = result.live_home_goals or 0
    live_away = result.live_away_goals or 0
    home = game.home_goals_announced
    away = game.away_goals_announced
    goal_posts: list[str] = []
    cancel_posts: list[str] = []
    while live_home > home:
        home += 1
        goal_posts.append(_goal_post(game, home, away, scoring_team=game.home_team_name))
    while live_home < home:
        home -= 1
        cancel_posts.append(_cancel_post(game, home, away, scoring_team=game.home_team_name))
    while live_away > away:
        away += 1
        goal_posts.append(_goal_post(game, home, away, scoring_team=game.away_team_name))
    while live_away < away:
        away -= 1
        cancel_posts.append(_cancel_post(game, home, away, scoring_team=game.away_team_name))
    game.home_goals_announced = live_home
    game.away_goals_announced = live_away
    game.goals_announced = live_home + live_away
    return goal_posts, cancel_posts


def _goal_post(game: Game, home_score: int, away_score: int, *, scoring_team: str) -> str:
    return goal_text(
        scoring_team=scoring_team,
        home_team=game.home_team_name,
        away_team=game.away_team_name,
        home_score=home_score,
        away_score=away_score,
    )


def _cancel_post(game: Game, home_score: int, away_score: int, *, scoring_team: str) -> str:
    return goal_cancelled_text(
        scoring_team=scoring_team,
        home_team=game.home_team_name,
        away_team=game.away_team_name,
        home_score=home_score,
        away_score=away_score,
    )


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

    # Finish/correct any bolãozinho whose last game this was (§22/§7).
    await resolve_and_post(app_context, context, fixture_id)


def schedule_poll_job(job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings) -> None:
    """Schedule live polling every ``poll_interval_seconds`` (§9.2)."""
    job_queue.run_repeating(
        poll_job,
        interval=settings.poll_interval_seconds,
        first=10,
        name=POLL_JOB_NAME,
    )
