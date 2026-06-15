"""Daily fixtures sync + group announcements (COMPLETION.md §9.1).

A ``JobQueue.run_daily`` job: one provider call (top budget priority), then for each fixture with
both real teams decided — insert new games (queue announcement), update rescheduled ones (bets stay
valid), and VOID postponed/cancelled ones (void their bets). New games get one consolidated group
announcement with a per-game 🎯 Apostar deep-link button; reschedules and voids are concise notices.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.keyboards import announcement_keyboard
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, utcnow
from tigrinho.db.repositories import BetRepository, GameRepository
from tigrinho.domain.text_pt import announcement_text, reannounce_text, void_text
from tigrinho.logging import get_logger
from tigrinho.providers.base import Fixture

_log = get_logger("tigrinho.sync_job")

SYNC_WINDOW_HOURS = 48
SYNC_JOB_NAME = "daily_sync"


def match_hash(fixture: Fixture) -> str:
    """Human-readable dedup label: ``sha256(kickoff_iso|home_team_id|away_team_id)`` (§6)."""
    raw = f"{fixture.kickoff_utc.isoformat()}|{fixture.home_team_id}|{fixture.away_team_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class SyncOutcome:
    """Games touched by a sync pass (ORM objects, attached to the sync session)."""

    new_games: list[Game] = field(default_factory=list)
    rescheduled_games: list[Game] = field(default_factory=list)
    voided_games: list[Game] = field(default_factory=list)


def _void_bets(bets_repo: BetRepository, fixture_id: int) -> None:
    now = utcnow()
    for bet in bets_repo.list_for_game(fixture_id):
        bet.is_correct = None
        bet.points_awarded = 0
        bet.settled_at = now


def sync_fixtures(session: Session, fixtures: Sequence[Fixture], *, tz: ZoneInfo) -> SyncOutcome:
    """Apply fixtures to the DB: insert new, reschedule changed, void postponed/cancelled."""
    games = GameRepository(session)
    bets = BetRepository(session)
    outcome = SyncOutcome()

    for fixture in fixtures:
        kickoff_utc = fixture.kickoff_utc.astimezone(UTC).replace(tzinfo=None)
        kickoff_local = fixture.kickoff_utc.astimezone(tz).replace(tzinfo=None)
        existing = games.get(fixture.fixture_id)

        if fixture.status in (GameStatus.POSTPONED, GameStatus.CANCELLED):
            if existing is not None and existing.status is not GameStatus.VOID:
                existing.status = GameStatus.VOID
                _void_bets(bets, existing.fixture_id)
                outcome.voided_games.append(existing)
            continue

        if existing is None:
            game = Game(
                fixture_id=fixture.fixture_id,
                match_hash=match_hash(fixture),
                stage=fixture.stage,
                home_team_id=fixture.home_team_id,
                home_team_name=fixture.home_team_name,
                away_team_id=fixture.away_team_id,
                away_team_name=fixture.away_team_name,
                kickoff_utc=kickoff_utc,
                kickoff_local=kickoff_local,
                status=GameStatus.SCHEDULED,
            )
            games.add(game)
            outcome.new_games.append(game)
        elif existing.status in (GameStatus.SCHEDULED, GameStatus.VOID) and (
            existing.kickoff_utc != kickoff_utc or existing.status is GameStatus.VOID
        ):
            # Rescheduled (kickoff changed) or un-voided after a postponement: bets stay valid for
            # the new time. LIVE/FINISHED games are never touched here (a re-sync must not reset a
            # game that has already kicked off).
            existing.kickoff_utc = kickoff_utc
            existing.kickoff_local = kickoff_local
            existing.match_hash = match_hash(fixture)
            existing.status = GameStatus.SCHEDULED
            outcome.rescheduled_games.append(existing)

    session.flush()
    return outcome


@dataclass(frozen=True, slots=True)
class _GameView:
    """Plain snapshot of a game for message building (decoupled from the session)."""

    fixture_id: int
    home_team_name: str
    away_team_name: str
    kickoff_local: datetime


def _view(game: Game) -> _GameView:
    return _GameView(
        fixture_id=game.fixture_id,
        home_team_name=game.home_team_name,
        away_team_name=game.away_team_name,
        kickoff_local=game.kickoff_local,
    )


async def _run_sync(
    app_context: AppContext,
) -> tuple[list[_GameView], list[_GameView], list[_GameView]]:
    """Fetch fixtures (budgeted) and apply them; returns (new, rescheduled, voided) snapshots."""
    fixtures = await app_context.budget.guarded(
        lambda: app_context.provider.get_fixtures(SYNC_WINDOW_HOURS)
    )
    with app_context.session_factory() as session:
        outcome = sync_fixtures(session, fixtures, tz=app_context.settings.tzinfo)
        new = [_view(g) for g in outcome.new_games]
        rescheduled = [_view(g) for g in outcome.rescheduled_games]
        voided = [_view(g) for g in outcome.voided_games]
        session.commit()
    return new, rescheduled, voided


async def sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily sync job callback (§9.1). One bad cycle never kills the bot (§14)."""
    app_context = get_app_context(context.application)
    settings = app_context.settings
    try:
        new, rescheduled, voided = await _run_sync(app_context)
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("sync_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot, settings.admin_user_id, f"⚠️ Sync falhou: <code>{exc}</code>"
        )
        return

    _log.info("sync_done", new=len(new), rescheduled=len(rescheduled), voided=len(voided))

    if new:
        text = announcement_text(
            [(g.home_team_name, g.away_team_name, g.kickoff_local) for g in new]
        )
        keyboard = announcement_keyboard(
            [(g.fixture_id, f"{g.home_team_name} x {g.away_team_name}") for g in new],
            settings.bot_username,
        )
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    for game in rescheduled:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=reannounce_text(game.home_team_name, game.away_team_name, game.kickoff_local),
            parse_mode=ParseMode.HTML,
        )

    for game in voided:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=void_text(game.home_team_name, game.away_team_name),
            parse_mode=ParseMode.HTML,
        )


def schedule_sync_job(job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings) -> None:
    """Schedule the daily fixtures sync at ``sync_time`` in the configured timezone (§9.1)."""
    run_time = settings.sync_time_obj.replace(tzinfo=settings.tzinfo)
    job_queue.run_daily(sync_job, time=run_time, name=SYNC_JOB_NAME)
