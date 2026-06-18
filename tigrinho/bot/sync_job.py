"""Daily fixtures sync + morning "next 24h" announcement (COMPLETION.md §9.1).

A ``JobQueue.run_daily`` job: one provider call (top budget priority), then for each fixture with
both real teams decided — insert new games, update rescheduled ones (bets stay valid), and VOID
postponed/cancelled ones (void their bets). New games are **not** announced as they are discovered;
instead each morning the job posts one consolidated announcement of the games kicking off in the
**next 24h** (with a per-game 🎯 Apostar deep-link button). Reschedules and voids are concise
notices.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.keyboards import announcement_keyboard
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.bot.tournament_announce import resolve_and_post
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, utcnow
from tigrinho.db.repositories import BetRepository, GameRepository
from tigrinho.domain.text_pt import (
    announcement_text,
    escape,
    reannounce_text,
    void_text,
)
from tigrinho.logging import get_logger
from tigrinho.providers.base import Fixture

_log = get_logger("tigrinho.sync_job")

SYNC_WINDOW_HOURS = 48
ANNOUNCE_HORIZON = timedelta(hours=24)
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


def _unvoid_bets(bets_repo: BetRepository, fixture_id: int) -> None:
    """Reset a previously-voided game's bets back to pending (mirrors ``BetRepository.upsert``).

    Without this, a postpone-then-reschedule leaves bets with ``settled_at`` + ``points_awarded=0``
    while the game is ``SCHEDULED`` again, leaking phantom 0-point rows onto the scoreboard (§9.1).
    """
    for bet in bets_repo.list_for_game(fixture_id):
        bet.is_correct = None
        bet.points_awarded = None
        bet.settled_at = None


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
            was_void = existing.status is GameStatus.VOID
            existing.kickoff_utc = kickoff_utc
            existing.kickoff_local = kickoff_local
            existing.match_hash = match_hash(fixture)
            existing.status = GameStatus.SCHEDULED
            existing.reminded_at = None
            if was_void:
                # Un-void: the game is pending again, so its bets must return to pending too.
                _unvoid_bets(bets, existing.fixture_id)
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


async def _run_sync(app_context: AppContext) -> tuple[list[_GameView], list[_GameView]]:
    """Fetch fixtures (budgeted) and apply them; returns (rescheduled, voided) snapshots.

    New games are not announced here — the morning "next 24h" announcement runs separately over the
    persisted ``announced_at IS NULL`` set, so a failed send is retried next morning (see
    :func:`_announce_upcoming_games`).
    """
    fixtures = await app_context.budget.guarded(
        lambda: app_context.provider.get_fixtures(SYNC_WINDOW_HOURS)
    )
    with app_context.session_factory() as session:
        outcome = sync_fixtures(session, fixtures, tz=app_context.settings.tzinfo)
        rescheduled = [_view(g) for g in outcome.rescheduled_games]
        voided = [_view(g) for g in outcome.voided_games]
        session.commit()
    return rescheduled, voided


async def _send_group(
    context: ContextTypes.DEFAULT_TYPE, settings: Settings, text: str, what: str
) -> bool:
    """Best-effort group post; on failure log + DM the admin so it is not lost silently."""
    try:
        await context.bot.send_message(
            chat_id=settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except TelegramError as exc:
        _log.error("group_send_failed", what=what, error=str(exc))
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Falha ao postar {what} no grupo: <code>{escape(str(exc))}</code>",
        )
        return False
    return True


async def _announce_upcoming_games(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Announce the still-unannounced games kicking off in the next 24h (§9.1).

    Only marks them announced on a successful send, so a failed send is retried next morning (the
    games are still inside the 24h window). ``announced_at`` also dedups across mornings.
    """
    settings = app_context.settings
    now = utcnow()
    with app_context.session_factory() as session:
        games = GameRepository(session).list_unannounced_within(now, ANNOUNCE_HORIZON)
        views = [_view(g) for g in games]
    if not views:
        return

    text = announcement_text([(v.home_team_name, v.away_team_name, v.kickoff_local) for v in views])
    keyboard = announcement_keyboard(
        [(v.fixture_id, f"{v.home_team_name} x {v.away_team_name}") for v in views],
        settings.bot_username,
    )
    try:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramError as exc:
        _log.error("announcement_failed", error=str(exc), count=len(views))
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Falha ao anunciar {len(views)} jogo(s) no grupo (será reenviado amanhã de manhã): "
            f"<code>{escape(str(exc))}</code>",
        )
        return

    with app_context.session_factory() as session:
        GameRepository(session).mark_announced([v.fixture_id for v in views], now)
        session.commit()
    _log.info("announced", count=len(views))


async def sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily sync job callback (§9.1). One bad cycle never kills the bot (§14)."""
    app_context = get_app_context(context.application)
    settings = app_context.settings
    try:
        rescheduled, voided = await _run_sync(app_context)
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("sync_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot, settings.admin_user_id, f"⚠️ Sync falhou: <code>{escape(str(exc))}</code>"
        )
        return

    _log.info("sync_done", rescheduled=len(rescheduled), voided=len(voided))

    await _announce_upcoming_games(app_context, context)

    for game in rescheduled:
        await _send_group(
            context,
            settings,
            reannounce_text(game.home_team_name, game.away_team_name, game.kickoff_local),
            f"reanúncio do jogo #{game.fixture_id}",
        )
    for game in voided:
        await _send_group(
            context,
            settings,
            void_text(game.home_team_name, game.away_team_name),
            f"anulação do jogo #{game.fixture_id}",
        )

    # A void can finish a bolãozinho (its last game; F4) and an un-void/reschedule can revive a
    # terminal one (F5) — re-evaluate every affected fixture's bolãozinhos (§22/§7).
    for game in [*voided, *rescheduled]:
        await resolve_and_post(app_context, context, game.fixture_id)


def schedule_sync_job(job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings) -> None:
    """Schedule the daily fixtures sync at ``sync_time`` in the configured timezone (§9.1)."""
    run_time = settings.sync_time_obj.replace(tzinfo=settings.tzinfo)
    job_queue.run_daily(sync_job, time=run_time, name=SYNC_JOB_NAME)
