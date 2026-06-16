"""Tests for the post-settlement reconcile job (COMPLETION.md §8.3, §9.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import ContextTypes

from tigrinho.bot.reconcile_job import CORRECTION_POST_CAP, reconcile_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import (
    ApiUsageRepository,
    BetRepository,
    GameRepository,
    PlayerRepository,
)
from tigrinho.providers.base import GoalEvent, MatchResult
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider

FIXTURE = 1489383


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


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


def _seed_settled_game(
    session_factory: sessionmaker[Session],
    *,
    home90: int = 3,
    away90: int = 0,
    settled_minutes_ago: float = 6,
    last_reconciled_at: datetime | None = None,
    kickoff_hours_ago: float = 3,
    status: GameStatus = GameStatus.FINISHED,
) -> None:
    kickoff = _now() - timedelta(hours=kickoff_hours_ago)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=FIXTURE,
                match_hash="h",
                stage=Stage.GROUP,
                home_team_id=2,
                home_team_name="France",
                away_team_id=13,
                away_team_name="Senegal",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=status,
                home_goals_90=home90,
                away_goals_90=away90,
                first_scorer_player_id=278,
                advancing_team_id=None,
                settled_at=_now() - timedelta(minutes=settled_minutes_ago),
                last_reconciled_at=last_reconciled_at,
            )
        )
        session.commit()


def _grade_bet(session: Session, *, player: int, name: str, category: str, payload: str) -> None:
    PlayerRepository(session).get_or_create(player, name)
    BetRepository(session).upsert(
        fixture_id=FIXTURE, player_telegram_id=player, category=category, payload_json=payload
    )


def _settle_bets_now(session_factory: sessionmaker[Session]) -> None:
    """Grade the seeded bets against the stored (pre-correction) score, as the first settle did."""
    from tigrinho.settlement_service import settle_fixture

    with session_factory() as session:
        game = GameRepository(session).get(FIXTURE)
        assert game is not None
        result = MatchResult(
            fixture_id=FIXTURE,
            stage=Stage.GROUP,
            status=GameStatus.FINISHED,
            home_goals_90=game.home_goals_90,
            away_goals_90=game.away_goals_90,
            goals=(_france_first_goal(),),
            advancing_team_id=None,
        )
        settle_fixture(session, game, result)
        # settle_fixture rewrites settled_at; re-anchor it to the due window the test intended.
        game.settled_at = _now() - timedelta(minutes=6)
        session.commit()


def _france_first_goal() -> GoalEvent:
    return GoalEvent(
        minute=66,
        team_id=2,
        player_id=278,
        player_name="Mbappe",
        is_own_goal=False,
        is_penalty=False,
    )


def _result(home: int, away: int, *, status: GameStatus = GameStatus.FINISHED) -> MatchResult:
    return MatchResult(
        fixture_id=FIXTURE,
        stage=Stage.GROUP,
        status=status,
        home_goals_90=None if status is not GameStatus.FINISHED else home,
        away_goals_90=None if status is not GameStatus.FINISHED else away,
        goals=(_france_first_goal(),),
        advancing_team_id=None,
    )


def _sent(bot: AsyncMock, *, chat_id: int) -> list[str]:
    return [
        c.kwargs["text"]
        for c in bot.send_message.await_args_list
        if c.kwargs.get("chat_id") == chat_id
    ]


async def test_reconcile_corrects_changed_score_and_posts_affected_only(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, home90=3, away90=0)
    with session_factory() as session:
        _grade_bet(session, player=1, name="Alice", category="WINNER", payload='{"sel":"HOME"}')
        _grade_bet(session, player=2, name="Bob", category="BTTS", payload='{"sel":"BOTH"}')
        session.commit()
    _settle_bets_now(session_factory)  # original (wrong) grading: 3-0

    provider = FakeProvider(results=[_result(3, 1)])  # API now reports France 3 x 1 Senegal
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await reconcile_job(context)

    with session_factory() as session:
        game = GameRepository(session).get(FIXTURE)
        assert game is not None
        assert (game.home_goals_90, game.away_goals_90) == (3, 1)
        assert game.last_reconciled_at is not None
        bob_bet = next(
            b for b in BetRepository(session).list_for_game(FIXTURE) if b.player_telegram_id == 2
        )
        assert bob_bet.points_awarded == 2  # BTTS BOTH now correct
    group_posts = _sent(bot, chat_id=settings.group_chat_id)
    assert len(group_posts) == 1
    text = group_posts[0]
    assert "Placar corrigido" in text
    assert "France 3 x 1 Senegal" in text
    assert "(antes: 3 x 0)" in text
    assert "tg://user?id=2" in text  # Bob (affected)
    assert "tg://user?id=1" not in text  # Alice (unaffected) not pinged
    assert app_context.reconcile_posts[FIXTURE] == 1


async def test_reconcile_no_change_advances_without_posting(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, home90=3, away90=0)
    with session_factory() as session:
        _grade_bet(session, player=1, name="Alice", category="WINNER", payload='{"sel":"HOME"}')
        session.commit()
    _settle_bets_now(session_factory)

    provider = FakeProvider(results=[_result(3, 0)])  # unchanged
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await reconcile_job(context)

    bot.send_message.assert_not_awaited()
    with session_factory() as session:
        game = GameRepository(session).get(FIXTURE)
        assert game is not None and game.last_reconciled_at is not None


async def test_reconcile_score_change_with_no_standing_move_is_silent(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, home90=3, away90=0)
    with session_factory() as session:
        _grade_bet(session, player=1, name="Alice", category="WINNER", payload='{"sel":"HOME"}')
        session.commit()
    _settle_bets_now(session_factory)

    provider = FakeProvider(results=[_result(4, 0)])  # 3-0 -> 4-0: WINNER HOME still correct
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await reconcile_job(context)

    bot.send_message.assert_not_awaited()  # no standing moved -> no group post
    with session_factory() as session:
        game = GameRepository(session).get(FIXTURE)
        assert game is not None and (game.home_goals_90, game.away_goals_90) == (
            4,
            0,
        )  # still re-graded


async def test_reconcile_not_due_makes_no_provider_call(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, settled_minutes_ago=1)  # < 5 min first-delay
    provider = FakeProvider(results=[_result(3, 1)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await reconcile_job(context)

    assert provider.call_log == []
    bot.send_message.assert_not_awaited()


async def test_reconcile_past_window_not_selected(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, kickoff_hours_ago=7)  # > 6h window
    provider = FakeProvider(results=[_result(3, 1)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await reconcile_job(context)

    assert provider.call_log == []


async def test_reconcile_provider_not_finished_does_not_advance(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, home90=3, away90=0)
    provider = FakeProvider(results=[_result(0, 0, status=GameStatus.LIVE)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await reconcile_job(context)

    bot.send_message.assert_not_awaited()
    with session_factory() as session:
        game = GameRepository(session).get(FIXTURE)
        assert game is not None
        assert game.last_reconciled_at is None  # transient read must NOT burn the cooldown
        assert (game.home_goals_90, game.away_goals_90) == (3, 0)  # unchanged


async def test_reconcile_yields_when_budget_below_reserve(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory)
    provider = FakeProvider(results=[_result(3, 1)])
    app_context = _app_context(settings, session_factory, provider)
    # api_daily_cap=100, reserve=25 -> push remaining below reserve.
    with session_factory() as session:
        ApiUsageRepository(session).increment(app_context.budget.today(), by=80)
        session.commit()
    context, bot = _context(app_context)

    await reconcile_job(context)

    assert provider.call_log == []  # whole pass yields, settlement budget preserved
    bot.send_message.assert_not_awaited()


async def test_reconcile_post_cap_reached_dms_admin_once(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_settled_game(session_factory, home90=3, away90=0)
    with session_factory() as session:
        _grade_bet(session, player=2, name="Bob", category="BTTS", payload='{"sel":"BOTH"}')
        session.commit()
    _settle_bets_now(session_factory)

    provider = FakeProvider(results=[_result(3, 1)])
    app_context = _app_context(settings, session_factory, provider)
    app_context.reconcile_posts[FIXTURE] = CORRECTION_POST_CAP  # cap already hit
    context, bot = _context(app_context)

    await reconcile_job(context)

    assert _sent(bot, chat_id=settings.group_chat_id) == []  # no further group post
    admin_dms = _sent(bot, chat_id=settings.admin_user_id)
    assert len(admin_dms) == 1  # admin told once
    with session_factory() as session:
        game = GameRepository(session).get(FIXTURE)
        assert game is not None and (game.home_goals_90, game.away_goals_90) == (
            3,
            1,
        )  # still re-graded
