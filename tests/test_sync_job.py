"""Tests for the daily sync job (COMPLETION.md §9.1, §16)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.bot.sync_job import (
    SYNC_JOB_NAME,
    match_hash,
    schedule_sync_job,
    sync_fixtures,
    sync_job,
)
from tigrinho.config import Settings
from tigrinho.db.models import GameStatus, Stage
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.providers.base import Fixture
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider

_TZ = ZoneInfo("America/Sao_Paulo")
_K1 = datetime(2026, 6, 16, 22, 0, tzinfo=UTC)
_K2 = datetime(2026, 6, 17, 22, 0, tzinfo=UTC)


def _fx(
    fixture_id: int,
    *,
    kickoff: datetime = _K1,
    status: GameStatus = GameStatus.SCHEDULED,
    stage: Stage = Stage.GROUP,
) -> Fixture:
    return Fixture(
        fixture_id=fixture_id,
        stage=stage,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=kickoff,
        status=status,
    )


def test_match_hash_is_deterministic() -> None:
    assert match_hash(_fx(1)) == match_hash(_fx(1))
    assert match_hash(_fx(1)) != match_hash(_fx(1, kickoff=_K2))


def test_sync_inserts_new_games(session: Session) -> None:
    outcome = sync_fixtures(session, [_fx(1)], tz=_TZ)
    assert len(outcome.new_games) == 1
    game = GameRepository(session).get(1)
    assert game is not None
    assert game.status is GameStatus.SCHEDULED
    assert game.kickoff_utc == datetime(2026, 6, 16, 22, 0)  # naive UTC
    assert game.kickoff_local == datetime(2026, 6, 16, 19, 0)  # São Paulo is UTC-3
    assert game.match_hash


def test_sync_reschedule_keeps_bets(session: Session) -> None:
    sync_fixtures(session, [_fx(1)], tz=_TZ)
    PlayerRepository(session).get_or_create(42, "A")
    BetRepository(session).upsert(
        fixture_id=1, player_telegram_id=42, category="WINNER", payload_json="{}"
    )

    outcome = sync_fixtures(session, [_fx(1, kickoff=_K2)], tz=_TZ)
    assert len(outcome.rescheduled_games) == 1
    game = GameRepository(session).get(1)
    assert game is not None
    assert game.kickoff_utc == datetime(2026, 6, 17, 22, 0)
    assert len(BetRepository(session).list_for_game(1)) == 1  # bet preserved


def test_sync_no_change_is_noop(session: Session) -> None:
    sync_fixtures(session, [_fx(1)], tz=_TZ)
    outcome = sync_fixtures(session, [_fx(1)], tz=_TZ)
    assert outcome.new_games == []
    assert outcome.rescheduled_games == []
    assert outcome.voided_games == []


def test_sync_voids_postponed_and_its_bets(session: Session) -> None:
    sync_fixtures(session, [_fx(1)], tz=_TZ)
    PlayerRepository(session).get_or_create(42, "A")
    BetRepository(session).upsert(
        fixture_id=1, player_telegram_id=42, category="WINNER", payload_json="{}"
    )

    outcome = sync_fixtures(session, [_fx(1, status=GameStatus.POSTPONED)], tz=_TZ)
    assert len(outcome.voided_games) == 1
    game = GameRepository(session).get(1)
    assert game is not None
    assert game.status is GameStatus.VOID
    bet = BetRepository(session).list_for_game(1)[0]
    assert bet.points_awarded == 0
    assert bet.settled_at is not None


def test_sync_voids_cancelled(session: Session) -> None:
    sync_fixtures(session, [_fx(1)], tz=_TZ)
    outcome = sync_fixtures(session, [_fx(1, status=GameStatus.CANCELLED)], tz=_TZ)
    assert len(outcome.voided_games) == 1
    game = GameRepository(session).get(1)
    assert game is not None
    assert game.status is GameStatus.VOID


def test_sync_unvoids_on_reschedule(session: Session) -> None:
    sync_fixtures(session, [_fx(1)], tz=_TZ)
    sync_fixtures(session, [_fx(1, status=GameStatus.POSTPONED)], tz=_TZ)
    outcome = sync_fixtures(session, [_fx(1, kickoff=_K2)], tz=_TZ)
    assert len(outcome.rescheduled_games) == 1
    game = GameRepository(session).get(1)
    assert game is not None
    assert game.status is GameStatus.SCHEDULED


def test_sync_ignores_postponed_unknown_fixture(session: Session) -> None:
    outcome = sync_fixtures(session, [_fx(99, status=GameStatus.POSTPONED)], tz=_TZ)
    assert outcome.voided_games == []
    assert outcome.new_games == []
    assert GameRepository(session).get(99) is None


def test_sync_does_not_reset_live_or_finished_game(session: Session) -> None:
    # A re-sync with the same kickoff must NOT reset a game that already kicked off (§9.1).
    sync_fixtures(session, [_fx(1)], tz=_TZ)
    games = GameRepository(session)
    live = games.get(1)
    assert live is not None
    live.status = GameStatus.LIVE
    session.flush()

    outcome = sync_fixtures(session, [_fx(1)], tz=_TZ)  # same kickoff
    assert outcome.rescheduled_games == []
    after = games.get(1)
    assert after is not None
    assert after.status is GameStatus.LIVE  # untouched


async def test_sync_job_announces_new_games(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    provider = FakeProvider(fixtures=[_fx(1), _fx(2)])
    budget = RequestBudget(
        session_factory, daily_cap=settings.api_daily_cap, reset_tz=settings.budget_tzinfo
    )
    app_context = AppContext(
        settings=settings, provider=provider, session_factory=session_factory, budget=budget
    )
    context = MagicMock()
    context.application.bot_data = {APP_CONTEXT_KEY: app_context}
    context.bot = AsyncMock()

    await sync_job(cast(ContextTypes.DEFAULT_TYPE, context))

    assert context.bot.send_message.await_count == 1  # one consolidated announcement
    call = context.bot.send_message.await_args
    assert call.kwargs["chat_id"] == settings.group_chat_id
    assert "Novos jogos" in call.kwargs["text"]
    assert budget.current_count() == 1  # exactly one provider call, via the budget
    with session_factory() as check:
        assert len(GameRepository(check).list_all()) == 2


def test_schedule_sync_job(settings: Settings) -> None:
    job_queue = MagicMock()
    schedule_sync_job(cast("JobQueue[ContextTypes.DEFAULT_TYPE]", job_queue), settings)
    job_queue.run_daily.assert_called_once()
    kwargs = job_queue.run_daily.call_args.kwargs
    assert kwargs["name"] == SYNC_JOB_NAME
    assert kwargs["time"].tzinfo is not None
