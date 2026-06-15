"""Tests for the live poll job + auto-settlement (COMPLETION.md §9.2, §16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import ContextTypes

from tigrinho.bot.poll_job import _settle_and_announce, poll_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.providers.base import GoalEvent, MatchResult
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed_game(
    session_factory: sessionmaker[Session],
    *,
    hours_ago: float,
    settled: bool = False,
    fixture_id: int = 1001,
) -> None:
    kickoff = _now() - timedelta(hours=hours_ago)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash="h",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.FINISHED if settled else GameStatus.SCHEDULED,
                settled_at=_now() if settled else None,
            )
        )
        session.commit()


def _finished_result(fixture_id: int = 1001) -> MatchResult:
    return MatchResult(
        fixture_id=fixture_id,
        stage=Stage.GROUP,
        status=GameStatus.FINISHED,
        home_goals_90=2,
        away_goals_90=1,
        goals=(
            GoalEvent(
                minute=10,
                team_id=10,
                player_id=100,
                player_name="Neymar",
                is_own_goal=False,
                is_penalty=False,
            ),
        ),
        advancing_team_id=None,
    )


def _app_context(
    settings: Settings, session_factory: sessionmaker[Session], provider: FakeProvider
) -> AppContext:
    budget = RequestBudget(
        session_factory, daily_cap=settings.api_daily_cap, reset_tz=settings.budget_tzinfo
    )
    return AppContext(
        settings=settings, provider=provider, session_factory=session_factory, budget=budget
    )


def _context(app_context: AppContext) -> tuple[ContextTypes.DEFAULT_TYPE, AsyncMock]:
    bot = AsyncMock()
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx), bot


async def test_poll_no_active_games_makes_no_api_call(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    provider = FakeProvider()  # no fixtures/results
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    assert provider.call_log == []  # NO API call when nothing is active
    assert app_context.budget.current_count() == 0
    bot.send_message.assert_not_awaited()


async def test_poll_settles_finished_game(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_game(session_factory, hours_ago=1)  # kicked off 1h ago -> active (window 3h)
    with session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Alice")
        BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        session.commit()

    provider = FakeProvider(results=[_finished_result()])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None
        assert game.status is GameStatus.FINISHED
        assert game.settled_at is not None
        bet = BetRepository(session).list_for_game(1001)[0]
        assert bet.points_awarded == 2  # home win
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == settings.group_chat_id
    # one get_live_results + one get_match_result
    assert app_context.budget.current_count() == 2


async def test_settle_skips_budget_when_already_settled(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_game(session_factory, hours_ago=1, settled=True)  # already settled
    provider = FakeProvider(results=[_finished_result()])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await _settle_and_announce(app_context, context, 1001)

    assert provider.call_log == []  # no get_match_result for an already-settled game
    assert app_context.budget.current_count() == 0
    bot.send_message.assert_not_awaited()


async def test_poll_settles_overdue_game_without_live_feed(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    # P1.2: a game past kickoff+SETTLE_AFTER settles via get_match_result alone — the live feed
    # (get_live_results) is never consulted, so a game that dropped out of live=all still settles.
    _seed_game(session_factory, hours_ago=2.5)  # within 3h window, past the 2h settle threshold
    provider = FakeProvider(results=[_finished_result()])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    assert provider.call_log == ["get_match_result:1001"]  # no get_live_results call
    bot.send_message.assert_awaited_once()
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.settled_at is not None


async def test_poll_settlement_runs_before_live_polling(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    # P2.4: settlement reads (overdue game) must precede the lower-priority live poll (§7.3).
    _seed_game(session_factory, fixture_id=1, hours_ago=2.5)  # overdue -> settle first
    _seed_game(session_factory, fixture_id=2, hours_ago=1)  # in progress -> live poll
    provider = FakeProvider(results=[_finished_result(1), _finished_result(2)])
    app_context = _app_context(settings, session_factory, provider)
    context, _bot = _context(app_context)

    await poll_job(context)

    # overdue game's settlement read comes first, then the live poll for the in-progress game.
    assert provider.call_log[0] == "get_match_result:1"
    assert provider.call_log[1] == "get_live_results"
    with session_factory() as session:
        assert GameRepository(session).get(1).settled_at is not None  # type: ignore[union-attr]
        assert GameRepository(session).get(2).settled_at is not None  # type: ignore[union-attr]


async def test_poll_does_not_settle_when_match_not_finished(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    # An overdue game whose provider result is still LIVE (e.g. extra time) must NOT settle.
    _seed_game(session_factory, hours_ago=2.5)
    live_result = MatchResult(
        fixture_id=1001,
        stage=Stage.GROUP,
        status=GameStatus.LIVE,
        home_goals_90=None,
        away_goals_90=None,
        goals=(),
        advancing_team_id=None,
    )
    provider = FakeProvider(results=[live_result])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    bot.send_message.assert_not_awaited()
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.settled_at is None
        assert game.status is GameStatus.LIVE  # status advanced, not settled


async def test_poll_alerts_admin_for_stuck_game(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_game(session_factory, hours_ago=5)  # past the 3h window, still unsettled -> stuck
    provider = FakeProvider()
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    bot.send_message.assert_awaited()  # admin alerted
    assert bot.send_message.await_args.kwargs["chat_id"] == settings.admin_user_id
    assert provider.call_log == []  # nothing active -> no provider call
