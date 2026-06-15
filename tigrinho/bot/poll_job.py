"""Live polling + auto-settlement (COMPLETION.md §9.2).

A ``JobQueue.run_repeating`` job. It returns **without any API call** when no game is in its live
window. When games are active it makes **one** ``get_live_results()`` call (budgeted); each game
that turned ``FINISHED`` is settled by fetching ``get_match_result()`` once (budgeted), grading the
bets, and posting one results message to the group. Games still unsettled past ``kickoff +
match_window_hours`` trigger an admin DM (manual settlement via CLI). Budget priority: settlement
reads beat further polling; the cap hard-stops polling and DMs the admin once per budget day (§7.3).
"""

from __future__ import annotations

from telegram.constants import ParseMode
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import alert_cap_reached, notify_admin
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import GameStatus, utcnow
from tigrinho.db.repositories import GameRepository, SquadRepository
from tigrinho.domain.text_pt import CATEGORY_LABELS, results_text
from tigrinho.logging import get_logger
from tigrinho.providers.budget import BudgetExceeded
from tigrinho.settlement_service import settle_fixture

_log = get_logger("tigrinho.poll_job")

POLL_JOB_NAME = "live_poll"


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
            context.bot, app_context.settings.admin_user_id, f"⚠️ Polling falhou: <code>{exc}</code>"
        )


async def _run_poll(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()

    with app_context.session_factory() as session:
        games = GameRepository(session)
        active_ids = [g.fixture_id for g in games.list_active(now, settings.match_window_hours)]
        stuck = [
            (g.fixture_id, f"{g.home_team_name} x {g.away_team_name}")
            for g in games.list_stuck(now, settings.match_window_hours)
        ]

    for fixture_id, label in stuck:
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⏳ Jogo {label} (#{fixture_id}) segue sem resultado após a janela. "
            "Pode precisar de settle manual via CLI.",
        )

    if not active_ids:
        return  # NO API call when nothing is active (§9.2)

    live = await app_context.budget.guarded(app_context.provider.get_live_results)
    live_by_id = {result.fixture_id: result for result in live}

    finished: list[int] = []
    with app_context.session_factory() as session:
        games = GameRepository(session)
        for fixture_id in active_ids:
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
    result = await app_context.budget.guarded(
        lambda: app_context.provider.get_match_result(fixture_id)
    )
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None or game.settled_at is not None:
            return  # already settled — idempotent, don't double-post
        summary = settle_fixture(session, game, result)
        scorer_name = None
        if summary.first_scorer_player_id is not None:
            scorer = SquadRepository(session).get(summary.first_scorer_player_id)
            scorer_name = scorer.name if scorer is not None else None
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
            scorer_name=scorer_name,
            players=players,
        )
        session.commit()

    await context.bot.send_message(
        chat_id=app_context.settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
    )


def schedule_poll_job(job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings) -> None:
    """Schedule live polling every ``poll_interval_minutes`` (§9.2)."""
    job_queue.run_repeating(
        poll_job,
        interval=settings.poll_interval_minutes * 60,
        first=10,
        name=POLL_JOB_NAME,
    )
