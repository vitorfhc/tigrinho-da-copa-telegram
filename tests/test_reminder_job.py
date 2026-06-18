"""Tests for the pre-game reminder job (COMPLETION.md §9.3, §16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session, sessionmaker
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho import tournament_service as svc
from tigrinho.bot.reminder_job import REMINDER_JOB_NAME, reminder_job, schedule_reminder_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.config import Settings
from tigrinho.db.models import Bet, Game, GameStatus, Player, Stage
from tigrinho.db.repositories import GameRepository
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed(
    session_factory: sessionmaker[Session],
    *,
    fixture_id: int,
    kickoff: datetime,
    announced: bool = True,
) -> None:
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=1,
                home_team_name="Brasil",
                away_team_id=2,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
                announced_at=_now() if announced else None,
            )
        )
        session.commit()


def _seed_bet(
    session_factory: sessionmaker[Session],
    *,
    fixture_id: int,
    telegram_id: int,
    name: str,
    category: str,
) -> None:
    with session_factory() as session:
        if session.get(Player, telegram_id) is None:
            session.add(Player(telegram_id=telegram_id, display_name=name))
        session.add(
            Bet(
                fixture_id=fixture_id,
                player_telegram_id=telegram_id,
                category=category,
                payload_json="{}",
            )
        )
        session.commit()


def _ctx(app_context: AppContext) -> tuple[MagicMock, AsyncMock]:
    context = MagicMock()
    context.application.bot_data = {APP_CONTEXT_KEY: app_context}
    bot = AsyncMock()
    context.bot = bot
    return context, bot


def _app_context(settings: Settings, session_factory: sessionmaker[Session]) -> AppContext:
    budget = RequestBudget(
        session_factory, daily_cap=settings.api_daily_cap, reset_tz=settings.budget_tzinfo
    )
    return AppContext(
        settings=settings, provider=FakeProvider(), session_factory=session_factory, budget=budget
    )


def _reminded_at(session_factory: sessionmaker[Session], fixture_id: int) -> datetime | None:
    """Read back a game's reminded_at (asserting it exists) — keeps assertions mypy-clean."""
    with session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        assert game is not None
        return game.reminded_at


async def test_combines_same_slot_into_one_message(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    slot = _now() + timedelta(minutes=30)
    _seed(session_factory, fixture_id=1, kickoff=slot)
    _seed(session_factory, fixture_id=2, kickoff=slot)  # identical kickoff -> same slot
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    assert bot.send_message.await_count == 1
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert len(markup.inline_keyboard) == 2  # one 🎯 Apostar button per game
    assert _reminded_at(session_factory, 1) is not None
    assert _reminded_at(session_factory, 2) is not None


async def test_staggered_slots_remind_separately_across_sweeps(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    base = _now()
    _seed(session_factory, fixture_id=1, kickoff=base + timedelta(minutes=30))  # soonest slot
    _seed(session_factory, fixture_id=2, kickoff=base + timedelta(minutes=45))  # later slot
    app_context = _app_context(settings, session_factory)

    context1, bot1 = _ctx(app_context)
    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context1))
    assert bot1.send_message.await_count == 1
    assert _reminded_at(session_factory, 1) is not None
    assert _reminded_at(session_factory, 2) is None  # later slot NOT yet reminded

    context2, bot2 = _ctx(app_context)
    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context2))
    assert bot2.send_message.await_count == 1  # the later slot now reminds
    assert _reminded_at(session_factory, 2) is not None


async def test_unannounced_game_is_not_reminded(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, kickoff=_now() + timedelta(minutes=30), announced=False)
    context, bot = _ctx(_app_context(settings, session_factory))

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    bot.send_message.assert_not_awaited()


async def test_no_due_games_posts_nothing(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, kickoff=_now() + timedelta(minutes=120))  # outside lead
    context, bot = _ctx(_app_context(settings, session_factory))

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    bot.send_message.assert_not_awaited()


async def test_send_failure_leaves_unflagged_and_alerts_admin(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, kickoff=_now() + timedelta(minutes=30))
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)
    bot.send_message.side_effect = TelegramError("group unreachable")

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    # The reminder send failed, then a best-effort admin DM was attempted (2 send_message calls).
    assert bot.send_message.await_count == 2
    assert _reminded_at(session_factory, 1) is None  # not flagged -> will retry next sweep


async def test_reminder_lists_bettors_ordered_by_count(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, kickoff=_now() + timedelta(minutes=30))
    # Felipe bet 3 categories; Ana bet all 5 -> Ana (5/5) sorts ahead of Felipe (3/5).
    for category in ("EXACT_SCORE", "FIRST_TEAM", "BTTS"):
        _seed_bet(session_factory, fixture_id=1, telegram_id=10, name="Felipe", category=category)
    for category in ("EXACT_SCORE", "FIRST_TEAM", "BTTS", "WINNER", "OVER_UNDER"):
        _seed_bet(session_factory, fixture_id=1, telegram_id=20, name="Ana", category=category)
    context, bot = _ctx(_app_context(settings, session_factory))

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    text = bot.send_message.await_args.kwargs["text"]
    assert "👥 Já palpitaram: Ana (5/5), Felipe (3/5)" in text


async def test_reminder_with_no_bettors_shows_nudge(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, kickoff=_now() + timedelta(minutes=30))
    context, bot = _ctx(_app_context(settings, session_factory))

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    text = bot.send_message.await_args.kwargs["text"]
    assert "Ninguém palpitou ainda" in text


def test_schedule_reminder_job(settings: Settings) -> None:
    job_queue = MagicMock()
    schedule_reminder_job(cast("JobQueue[ContextTypes.DEFAULT_TYPE]", job_queue), settings)
    job_queue.run_repeating.assert_called_once()
    kwargs = job_queue.run_repeating.call_args.kwargs
    assert kwargs["name"] == REMINDER_JOB_NAME
    assert kwargs["interval"] == settings.reminder_interval_minutes * 60


def _make_tournament(
    session_factory: sessionmaker[Session],
    *,
    fixture_id: int,
    entrants: list[tuple[int, str]],
) -> None:
    with session_factory() as session:
        tournament = svc.create_tournament(
            session, name="Oitavas", entry_price_cents=1000, created_by=7
        )
        svc.add_game(session, tournament, fixture_id, now=_now())
        svc.open_tournament(session, tournament, now=_now())
        for telegram_id, name in entrants:
            svc.join(session, tournament, telegram_id=telegram_id, display_name=name, now=_now())
        session.commit()


async def test_reminder_mentions_non_betting_entrant(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    slot = _now() + timedelta(minutes=30)
    _seed(session_factory, fixture_id=1, kickoff=slot)
    _make_tournament(session_factory, fixture_id=1, entrants=[(100, "Ana"), (200, "Bruno")])
    _seed_bet(session_factory, fixture_id=1, telegram_id=100, name="Ana", category="WINNER")
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    text = bot.send_message.await_args.kwargs["text"]
    assert "Vale pelo bolãozinho" in text
    assert "tg://user?id=200" in text  # Bruno hasn't bet -> mentioned
    assert "corre!" in text


async def test_reminder_caps_mentions(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    slot = _now() + timedelta(minutes=30)
    _seed(session_factory, fixture_id=1, kickoff=slot)
    entrants = [(1000 + i, f"P{i:02d}") for i in range(25)]  # 25 > reminder_max_mentions (20)
    _make_tournament(session_factory, fixture_id=1, entrants=entrants)
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    text = bot.send_message.await_args.kwargs["text"]
    assert "+5" in text  # 25 entrants, 20 shown


async def test_reminder_includes_unannounced_tournament_game(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    slot = _now() + timedelta(minutes=30)
    _seed(session_factory, fixture_id=1, kickoff=slot, announced=False)
    _make_tournament(session_factory, fixture_id=1, entrants=[(100, "Ana")])
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    assert bot.send_message.await_count == 1  # eligible despite announced_at IS NULL (§22)
    assert _reminded_at(session_factory, 1) is not None
