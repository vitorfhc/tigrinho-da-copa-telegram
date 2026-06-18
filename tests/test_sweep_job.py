"""Tests for the bolãozinho sweep job (Feature 7 / §22, §7 — F4/F12/F13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ContextTypes

from tigrinho import tournament_service as svc
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.bot.sweep_job import sweep_job
from tigrinho.db.models import Game, GameStatus, Stage, TournamentStatus
from tigrinho.db.repositories import BetRepository, TournamentRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed_game(app_context: AppContext, fixture_id: int, *, hours: int) -> None:
    kickoff = _now() + timedelta(hours=hours)
    with app_context.session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
            )
        )
        session.commit()


def _open_tournament(app_context: AppContext, fixture_id: int) -> int:
    with app_context.session_factory() as session:
        tournament = svc.create_tournament(
            session, name="Oitavas", entry_price_cents=1000, created_by=7
        )
        svc.add_game(session, tournament, fixture_id, now=_now())
        svc.open_tournament(session, tournament, now=_now())
        session.commit()
        return tournament.id


def _context(app_context: AppContext) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.bot = AsyncMock()
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


async def test_sweep_locks_after_kickoff(app_context: AppContext) -> None:
    _seed_game(app_context, 1, hours=3)
    tid = _open_tournament(app_context, 1)
    # Move kickoff into the past, then sweep — the lock must engage (F12).
    with app_context.session_factory() as session:
        session.get(Game, 1).kickoff_utc = _now() - timedelta(minutes=5)  # type: ignore[union-attr]
        session.commit()
    await sweep_job(_context(app_context))
    with app_context.session_factory() as session:
        assert TournamentRepository(session).get(tid).locked_at is not None  # type: ignore[union-attr]


async def test_sweep_finishes_unannounced_resolved_tournament(app_context: AppContext) -> None:
    """F4: last game resolved (e.g. VOIDed in sync) but never announced — sweep finishes it."""
    _seed_game(app_context, 1, hours=3)
    tid = _open_tournament(app_context, 1)
    with app_context.session_factory() as session:
        # A player joins, bets, the game finishes & is graded — but the announcement never fired.
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        svc.join(session, tournament, telegram_id=100, display_name="Ana", now=_now())
        bet = BetRepository(session).upsert(
            fixture_id=1, player_telegram_id=100, category="WINNER", payload_json="{}"
        )
        bet.is_correct = True
        bet.points_awarded = 2
        bet.settled_at = _now()
        game = session.get(Game, 1)
        assert game is not None
        game.status = GameStatus.FINISHED
        game.settled_at = _now()
        session.commit()
    ctx = _context(app_context)
    await sweep_job(ctx)
    ctx.bot.send_message.assert_awaited()  # type: ignore[attr-defined]
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        assert tournament.status is TournamentStatus.FINISHED
        assert tournament.result_announced_at is not None


async def test_sweep_alerts_stranded_once(app_context: AppContext) -> None:
    """F13: a member game stranded past its window DMs the admin once (not every cycle)."""
    _seed_game(app_context, 1, hours=3)
    tid = _open_tournament(app_context, 1)
    with app_context.session_factory() as session:
        game = session.get(Game, 1)
        assert game is not None
        game.kickoff_utc = _now() - timedelta(hours=10)  # well past the match window, unsettled
        session.commit()
    ctx = _context(app_context)
    await sweep_job(ctx)
    assert ctx.bot.send_message.await_count == 1  # type: ignore[attr-defined]
    # Second sweep: already alerted -> no new DM (deduped).
    await sweep_job(ctx)
    assert ctx.bot.send_message.await_count == 1  # type: ignore[attr-defined]
    assert tid in app_context.tournament_stuck_alerted
