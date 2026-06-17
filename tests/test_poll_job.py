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
from tigrinho.providers.base import GoalEvent, MatchResult, VarCancellation
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


def _seed_live_game(
    session_factory: sessionmaker[Session],
    *,
    fixture_id: int = 1001,
    started: bool = True,
    goals_announced: int = 0,
    hours_ago: float = 0.5,
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
                status=GameStatus.LIVE if started else GameStatus.SCHEDULED,
                started_at=_now() if started else None,
                goals_announced=goals_announced,
            )
        )
        session.commit()


def _live_result(
    *, fixture_id: int = 1001, home: int = 0, away: int = 0, status: GameStatus = GameStatus.LIVE
) -> MatchResult:
    return MatchResult(
        fixture_id=fixture_id,
        stage=Stage.GROUP,
        status=status,
        home_goals_90=None,
        away_goals_90=None,
        goals=(),
        advancing_team_id=None,
        live_home_goals=home,
        live_away_goals=away,
    )


def _goal(minute: int, team_id: int, *, name: str | None = None, own: bool = False) -> GoalEvent:
    return GoalEvent(
        minute=minute,
        team_id=team_id,
        player_id=None,
        player_name=name,
        is_own_goal=own,
        is_penalty=False,
    )


def _cancel(
    minute: int, team_id: int, *, name: str | None = None, detail: str = "Goal cancelled"
) -> VarCancellation:
    return VarCancellation(minute=minute, team_id=team_id, player_name=name, detail=detail)


def _sent_texts(bot: AsyncMock) -> list[str]:
    return [call.kwargs["text"] for call in bot.send_message.await_args_list]


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


async def test_stuck_game_admin_alert_is_deduped_across_cycles(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    # The same stuck game must alert the admin once, not on every ~10-min poll cycle (§9.2).
    _seed_game(session_factory, hours_ago=5)  # past the 3h window, still unsettled -> stuck
    provider = FakeProvider()
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)
    await poll_job(context)  # second sweep, same stuck game still stuck

    stuck_alerts = [
        c
        for c in bot.send_message.await_args_list
        if c.kwargs.get("chat_id") == settings.admin_user_id
    ]
    assert len(stuck_alerts) == 1  # deduped — not one per cycle


async def test_kickoff_announced_once(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=False, goals_announced=0)  # SCHEDULED, not started
    provider = FakeProvider(results=[_live_result(home=0, away=0)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)
    assert any("Bola rolando" in t for t in _sent_texts(bot))
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.started_at is not None

    bot.send_message.reset_mock()
    await poll_job(context)  # second cycle: already started, score still 0-0
    assert not any("Bola rolando" in t for t in _sent_texts(bot))


async def test_kickoff_not_announced_when_first_seen_finished(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=False)  # SCHEDULED
    provider = FakeProvider(results=[_finished_result()])  # feed reports FINISHED
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)
    assert not any("Bola rolando" in t for t in _sent_texts(bot))
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.started_at is None


async def test_kickoff_reveals_everyones_bets(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=False, goals_announced=0)  # SCHEDULED, not started
    with session_factory() as session:
        players = PlayerRepository(session)
        bets = BetRepository(session)
        players.get_or_create(42, "Felipe")
        players.get_or_create(7, "Ana")
        bets.upsert(
            fixture_id=1001,
            player_telegram_id=42,
            category="EXACT_SCORE",
            payload_json='{"home":2,"away":1}',
        )
        bets.upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        bets.upsert(
            fixture_id=1001, player_telegram_id=7, category="WINNER", payload_json='{"sel":"AWAY"}'
        )
        session.commit()

    provider = FakeProvider(results=[_live_result(home=0, away=0)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    texts = _sent_texts(bot)
    kickoff = next(t for t in texts if "Bola rolando" in t)
    reveal = next(t for t in texts if "Apostas fechadas" in t)
    # The reveal rides right after the kickoff post, to the group.
    assert texts.index(kickoff) < texts.index(reveal)
    assert all(
        call.kwargs["chat_id"] == settings.group_chat_id
        for call in bot.send_message.await_args_list
    )
    # Grouped by category, with each bettor's selection.
    assert "Placar exato" in reveal
    assert "Vencedor" in reveal
    assert "Felipe: 2x1" in reveal
    assert "Felipe: Brasil" in reveal  # WINNER HOME -> home team name
    assert "Ana: Argentina" in reveal  # WINNER AWAY -> away team name


async def test_kickoff_with_no_bets_posts_only_kickoff(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=False, goals_announced=0)  # SCHEDULED, no bets
    provider = FakeProvider(results=[_live_result(home=0, away=0)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    texts = _sent_texts(bot)
    assert any("Bola rolando" in t for t in texts)
    assert not any("Apostas fechadas" in t for t in texts)


async def test_goal_announced_on_score_increase(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=0)
    provider = FakeProvider(
        results=[_live_result(home=1, away=0)],
        goal_events={1001: (_goal(23, 10, name="Vini Jr"),)},
    )
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    texts = _sent_texts(bot)
    assert any("GOL do Brasil" in t and "1 x 0" in t and "Vini Jr" in t for t in texts)
    assert "get_goal_events:1001" in provider.call_log
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 1


async def test_no_event_fetch_when_score_unchanged(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=1)
    provider = FakeProvider(results=[_live_result(home=1, away=0)])  # total 1 == goals_announced
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    assert not any(c.startswith("get_goal_events") for c in provider.call_log)
    assert not any("GOL" in t for t in _sent_texts(bot))


async def test_var_disallowed_goal_announced_with_reason_and_resyncs(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=1)
    provider = FakeProvider(
        results=[_live_result(home=0, away=0)],  # total 0 < goals_announced 1 → a goal vanished
        goal_cancellations={
            1001: (_cancel(8, 20, name="Messi", detail="Goal Disallowed - offside"),)
        },
    )
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    texts = _sent_texts(bot)
    cancel_texts = [t for t in texts if "anulado pelo VAR" in t]
    assert len(cancel_texts) == 1
    assert "Gol do Argentina" in cancel_texts[0]  # team_id 20 == away "Argentina"
    assert "Messi" in cancel_texts[0]
    assert "impedimento" in cancel_texts[0]
    assert "Brasil 0 x 0 Argentina" in cancel_texts[0]
    assert "get_goal_cancellations:1001" in provider.call_log
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 0


async def test_var_disallowed_goal_announced_generically_when_event_absent(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    # Live score already dropped, but the Var event hasn't surfaced in the feed yet (observed lag).
    _seed_live_game(session_factory, started=True, goals_announced=1)
    provider = FakeProvider(
        results=[_live_result(home=0, away=0)]
    )  # no goal_cancellations scripted
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    cancel_texts = [t for t in _sent_texts(bot) if "anulado pelo VAR" in t]
    assert len(cancel_texts) == 1
    assert "Gol do" not in cancel_texts[0]  # generic — no team/scorer detail available
    assert "Brasil 0 x 0 Argentina" in cancel_texts[0]
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 0


async def test_cancellation_not_reannounced_once_synced(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    # Cursor already resynced down to the live total — no further retraction, no event fetch.
    _seed_live_game(session_factory, started=True, goals_announced=0)
    provider = FakeProvider(
        results=[_live_result(home=0, away=0)],
        goal_cancellations={1001: (_cancel(8, 20, name="Messi"),)},
    )
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    assert not any("anulado pelo VAR" in t for t in _sent_texts(bot))
    assert not any(c.startswith("get_goal_cancellations") for c in provider.call_log)


async def test_multiple_new_goals_posted_in_order(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=0)
    provider = FakeProvider(
        results=[_live_result(home=1, away=1)],
        goal_events={1001: (_goal(10, 10, name="Home One"), _goal(25, 20, name="Away One"))},
    )
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    goal_texts = [t for t in _sent_texts(bot) if "GOL" in t]
    assert len(goal_texts) == 2
    assert "1 x 0" in goal_texts[0]
    assert "1 x 1" in goal_texts[1]
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 2
