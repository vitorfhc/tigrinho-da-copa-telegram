"""Bolãozinho sweep job — lock / finish / rescue backstop (Feature 7 / §22, §7).

A ``JobQueue.run_repeating`` job that makes no provider calls. Each tick it:
1. Persists the one-way first-kickoff **lock** on OPEN bolãozinhos whose earliest game kicked off
   (freezes games/price/joins; F12).
2. **Finishes** any OPEN bolãozinho whose member games are all resolved but was never announced —
   covers the case where the last unresolved game became VOID outside a settlement path (F4).
3. **Escalates** to the admin (once per bolãozinho) when a member game is stranded past its match
   window unsettled, so a real-money pot is never silently stuck (F13).

One bad cycle never kills the bot (§14).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from telegram.ext import ContextTypes, JobQueue

from tigrinho import tournament_service as svc
from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.bot.splitwise_register import register_finished_tournament
from tigrinho.bot.tournament_announce import resolve_and_post
from tigrinho.config import Settings
from tigrinho.db.models import GameStatus, TournamentStatus, utcnow
from tigrinho.db.repositories import TournamentRepository
from tigrinho.domain.text_pt import escape, splitwise_admin_ready_text
from tigrinho.logging import get_logger
from tigrinho.splitwise_service import finished_auto_tournaments, manual_ready_to_notify

_log = get_logger("tigrinho.sweep_job")

SWEEP_JOB_NAME = "bolaozinho_sweep"


async def sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sweep callback (§22/§7). One bad cycle must not kill the bot (§14)."""
    app_context = get_app_context(context.application)
    try:
        await _run_sweep(app_context, context)
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("sweep_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Sweep de bolãozinho falhou: <code>{escape(str(exc))}</code>",
        )


def _is_stranded(
    *,
    kickoff_utc: datetime,
    settled_at: datetime | None,
    status: GameStatus,
    now: datetime,
    window_hours: int,
) -> bool:
    return (
        settled_at is None
        and status in (GameStatus.SCHEDULED, GameStatus.LIVE)
        and kickoff_utc < now - timedelta(hours=window_hours)
    )


async def _run_sweep(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()
    to_resolve: list[int] = []
    stranded: list[tuple[int, str, int, str]] = []  # (tournament_id, name, fixture_id, label)

    with app_context.session_factory() as session:
        repo = TournamentRepository(session)
        for tournament in repo.list_by_status(TournamentStatus.DRAFT, TournamentStatus.OPEN):
            svc.ensure_lock(session, tournament, now)
            if tournament.status is not TournamentStatus.OPEN:
                continue
            if tournament.result_announced_at is not None:
                continue
            if repo.all_games_resolved(tournament.id):
                game_ids = repo.list_game_ids(tournament.id)
                if game_ids:
                    to_resolve.append(game_ids[0])
                continue
            for game in repo.list_games(tournament.id):
                if _is_stranded(
                    kickoff_utc=game.kickoff_utc,
                    settled_at=game.settled_at,
                    status=game.status,
                    now=now,
                    window_hours=settings.match_window_hours,
                ):
                    label = f"{game.home_team_name} x {game.away_team_name}"
                    stranded.append((tournament.id, tournament.name, game.fixture_id, label))
                    break
        session.commit()

    for fixture_id in to_resolve:
        await resolve_and_post(app_context, context, fixture_id)

    # Alert the admin once per stranded bolãozinho; prune ids that recovered so they can re-alert.
    alerted = app_context.tournament_stuck_alerted
    alerted.intersection_update({tid for tid, _, _, _ in stranded})
    for tournament_id, name, fixture_id, label in stranded:
        if tournament_id in alerted:
            continue
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⏳ Bolãozinho <b>{escape(name)}</b> (#{tournament_id}) travado no jogo "
            f"{escape(label)} (#{fixture_id}) — pode precisar de settle/cancel manual via CLI.",
        )
        alerted.add(tournament_id)
    if to_resolve or stranded:
        _log.info("swept", resolved=len(to_resolve), stranded=len(stranded))

    await _splitwise_sweep(app_context, context)


async def _splitwise_sweep(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retry unsynced AUTO registrations and DM the admin once per now-ready MANUAL one (§23)."""
    if not app_context.settings.splitwise_enabled:
        return
    # AUTO: retry registration for finished AUTO bolãozinhos (build_registration no-ops if synced).
    with app_context.session_factory() as session:
        auto = [
            (t.id, t.splitwise_expense_id is not None) for t in finished_auto_tournaments(session)
        ]
    for tournament_id, has_expense in auto:
        await register_finished_tournament(
            app_context, context, tournament_id, is_correction=has_expense
        )
    # MANUAL: mark + notify the admin once for each newly fully-linked, not-yet-registered one.
    with app_context.session_factory() as session:
        ready = manual_ready_to_notify(session)
        notes = [(t.id, t.name) for t in ready]
        for tournament in ready:
            tournament.splitwise_admin_notified_at = utcnow()
        session.commit()
    for tournament_id, name in notes:
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            splitwise_admin_ready_text(tournament_id=tournament_id, name=name),
        )


def schedule_sweep_job(job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings) -> None:
    """Schedule the bolãozinho sweep every ``bolaozinho_sweep_interval_minutes`` (§22)."""
    job_queue.run_repeating(
        sweep_job,
        interval=settings.bolaozinho_sweep_interval_minutes * 60,
        first=30,
        name=SWEEP_JOB_NAME,
    )
