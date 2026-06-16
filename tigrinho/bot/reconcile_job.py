"""Post-settlement score reconciliation (COMPLETION.md §8.3, §9.2).

A ``JobQueue.run_repeating`` job, separate from the live poll (§9.2) and acting on **already
settled** games. It exists because the provider feed can finalize a game's ``score.fulltime`` late
(a stoppage-time or VAR-reviewed goal ingested minutes after the final whistle): the poll job
settles on a single read and is idempotent, so a too-early read freezes a wrong score forever.

For a bounded window after kickoff (``reconcile_window_hours``) the job re-reads each settled
game's result and, if the graded **outcome** changed (90′ score, first scorer, advancing team,
or any bet's grade), re-grades via the idempotent :func:`settle_fixture`. Only when a player's
total actually moved does it post a "Placar corrigido" correction to the group. Reconcile is the
**lowest** budget priority (§7.3): it yields the whole pass when fewer than
``reconcile_budget_reserve`` requests remain, so it can never starve real-time settlement.

Per-game cadence (backoff): first check ~``reconcile_first_delay_minutes`` after settlement, then
every ``reconcile_interval_minutes``, tracked by ``games.last_reconciled_at`` (survives restarts).
A transient read (provider not FINISHED / incomplete) does **not** advance the cursor, so the next
base tick retries quickly during the in-flux window.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import alert_cap_reached, notify_admin
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, utcnow
from tigrinho.db.repositories import BetRepository, GameRepository
from tigrinho.domain.bets import BetCategory
from tigrinho.domain.scoring import first_genuine_scorer
from tigrinho.domain.settlement import PendingBet, settle_game
from tigrinho.domain.text_pt import CATEGORY_LABELS, correction_text, escape
from tigrinho.logging import get_logger
from tigrinho.providers.budget import BudgetExceeded
from tigrinho.settlement_service import PlayerResult, SettlementSummary, settle_fixture

_log = get_logger("tigrinho.reconcile_job")

RECONCILE_JOB_NAME = "reconcile"
# Most automatic group corrections per game; beyond it the bot re-grades silently and DMs the admin
# once, so an oscillating (VAR) feed cannot spam the group with contradictory posts (§8.3).
CORRECTION_POST_CAP = 2


async def reconcile_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-check settled games for late provider corrections (§8.3). Never kills the bot (§14)."""
    app_context = get_app_context(context.application)
    try:
        await _run_reconcile(app_context, context)
    except BudgetExceeded:
        await alert_cap_reached(app_context, context.bot, app_context.budget.today())
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("reconcile_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Reconciliação falhou: <code>{escape(str(exc))}</code>",
        )


async def _run_reconcile(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()

    # Lowest budget priority (§7.3): leave headroom for sync + live polling + settlement reads.
    if app_context.budget.remaining() < settings.reconcile_budget_reserve:
        _log.info("reconcile_skipped_low_budget", remaining=app_context.budget.remaining())
        return

    first_delay = timedelta(minutes=settings.reconcile_first_delay_minutes)
    steady = timedelta(minutes=settings.reconcile_interval_minutes)
    with app_context.session_factory() as session:
        games = GameRepository(session).list_reconcilable(now, settings.reconcile_window_hours)
        due = [g.fixture_id for g in games if _is_due(g, now, first_delay, steady)]

    for fixture_id in due:
        await _reconcile_one(app_context, context, fixture_id, now)


def _is_due(game: Game, now: datetime, first_delay: timedelta, steady: timedelta) -> bool:
    if game.last_reconciled_at is None:
        return game.settled_at is not None and now >= game.settled_at + first_delay
    return now >= game.last_reconciled_at + steady


async def _reconcile_one(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, fixture_id: int, now: datetime
) -> None:
    result = await app_context.budget.guarded(
        lambda: app_context.provider.get_match_result(fixture_id)
    )

    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        # Write-time re-assert: skip if voided/rescheduled/un-settled since it was listed.
        if game is None or game.status is not GameStatus.FINISHED or game.settled_at is None:
            return
        # Transient/incomplete provider read: do NOT advance the cursor — retry next base tick.
        if (
            result.status is not GameStatus.FINISHED
            or result.home_goals_90 is None
            or result.away_goals_90 is None
        ):
            return

        bets = BetRepository(session).list_for_game(fixture_id)
        pending = [PendingBet(b.id, BetCategory(b.category), b.payload_json) for b in bets]
        graded = {
            g.bet_id: g
            for g in settle_game(
                pending, result, home_team_id=game.home_team_id, away_team_id=game.away_team_id
            )
        }
        new_first = first_genuine_scorer(result.goals)
        new_first_id = new_first.player_id if new_first is not None else None
        score_changed = (game.home_goals_90, game.away_goals_90) != (
            result.home_goals_90,
            result.away_goals_90,
        )
        outcome_changed = (
            score_changed
            or game.advancing_team_id != result.advancing_team_id
            or game.first_scorer_player_id != new_first_id
            or any(
                b.points_awarded != graded[b.id].points or b.is_correct != graded[b.id].is_correct
                for b in bets
            )
        )
        if not outcome_changed:
            game.last_reconciled_at = now
            session.commit()
            return

        prev_score = (game.home_goals_90 or 0, game.away_goals_90 or 0)
        old_totals: dict[int, int] = {}
        for b in bets:
            old_totals[b.player_telegram_id] = old_totals.get(b.player_telegram_id, 0) + (
                b.points_awarded or 0
            )
        summary = settle_fixture(session, game, result)
        game.last_reconciled_at = now
        session.commit()

    affected = [p for p in summary.players if p.total_points != old_totals.get(p.telegram_id, 0)]
    if not affected:
        _log.info("reconciled_silent", fixture_id=fixture_id)  # re-graded, no standing moved
        return

    await _post_correction(
        app_context, context, fixture_id, summary, old_totals, affected, score_changed, prev_score
    )


async def _post_correction(
    app_context: AppContext,
    context: ContextTypes.DEFAULT_TYPE,
    fixture_id: int,
    summary: SettlementSummary,
    old_totals: dict[int, int],
    affected: list[PlayerResult],
    score_changed: bool,
    prev_score: tuple[int, int],
) -> None:
    posts = app_context.reconcile_posts.get(fixture_id, 0)
    if posts >= CORRECTION_POST_CAP:
        if posts == CORRECTION_POST_CAP:  # DM the admin exactly once, then stay silent
            app_context.reconcile_posts[fixture_id] = posts + 1
            await notify_admin(
                context.bot,
                app_context.settings.admin_user_id,
                f"⚠️ Jogo #{fixture_id} recalculado de novo, mas o limite de correções no grupo "
                "foi atingido. Confira via /placar_jogo.",
            )
        return

    players = [
        (
            p.telegram_id,
            p.display_name,
            old_totals.get(p.telegram_id, 0),
            p.total_points,
            [(CATEGORY_LABELS[c.category], c.is_correct, c.points) for c in p.categories],
        )
        for p in affected
    ]
    text = correction_text(
        home=summary.home_team_name,
        away=summary.away_team_name,
        home_goals=summary.home_goals_90,
        away_goals=summary.away_goals_90,
        corrected_from=prev_score if score_changed else None,
        first_team_name=summary.first_scoring_team_name,
        players=players,
    )
    try:
        await context.bot.send_message(
            chat_id=app_context.settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
        )
        app_context.reconcile_posts[fixture_id] = posts + 1
        _log.info("reconciled_corrected", fixture_id=fixture_id, affected=len(affected))
    except TelegramError as exc:
        _log.error("correction_post_failed", fixture_id=fixture_id, error=str(exc))
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Jogo #{fixture_id} recalculado, mas falhou ao postar a correção: "
            f"<code>{escape(str(exc))}</code>",
        )


def schedule_reconcile_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the reconcile job; its base tick is ``reconcile_first_delay_minutes`` (§8.3)."""
    job_queue.run_repeating(
        reconcile_job,
        interval=settings.reconcile_first_delay_minutes * 60,
        first=75,
        name=RECONCILE_JOB_NAME,
    )
