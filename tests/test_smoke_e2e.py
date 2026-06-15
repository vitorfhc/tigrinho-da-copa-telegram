"""End-to-end smoke test with provider_mode: fake (COMPLETION.md §0 DoD, §11).

Drives the real components — daily sync → bet via the deep-link wizard → live poll + auto-settlement
→ group results → scoreboard — against a temp SQLite DB and a scripted FakeProvider, asserting the
whole pipeline works without error. Also verifies budget enforcement end-to-end (hard stop at cap).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session, sessionmaker
from telegram import CallbackQuery, Chat, Update, User
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from tigrinho.bot.bets_handlers import on_callback, start_handler
from tigrinho.bot.board_handlers import placar_handler
from tigrinho.bot.callbacks import WinnerInput, encode
from tigrinho.bot.poll_job import poll_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.bot.sync_job import sync_job
from tigrinho.config import Settings
from tigrinho.db.repositories import BetRepository, GameRepository
from tigrinho.domain.bets import WinnerSel
from tigrinho.enums import GameStatus, Stage
from tigrinho.providers.base import Fixture, GoalEvent, MatchResult
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider

_FID = 5001
_USER = User(id=42, is_bot=False, first_name="Alice")


def _now_naive() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _fixture() -> Fixture:
    return Fixture(
        fixture_id=_FID,
        stage=Stage.GROUP,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=datetime.now(tz=UTC) + timedelta(hours=2),
        status=GameStatus.SCHEDULED,
    )


def _result() -> MatchResult:
    return MatchResult(
        fixture_id=_FID,
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
    settings: Settings, session_factory: sessionmaker[Session], provider: FakeProvider, cap: int
) -> AppContext:
    budget = RequestBudget(session_factory, daily_cap=cap, reset_tz=settings.budget_tzinfo)
    return AppContext(
        settings=settings, provider=provider, session_factory=session_factory, budget=budget
    )


def _job_context(app_context: AppContext) -> tuple[ContextTypes.DEFAULT_TYPE, AsyncMock]:
    bot = AsyncMock()
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx), bot


def _start_update() -> tuple[Update, AsyncMock]:
    message = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = _USER
    update.effective_chat = Chat(id=1, type=ChatType.PRIVATE)
    return cast(Update, update), message


def _cb_update(data: str) -> Update:
    query = AsyncMock(spec=CallbackQuery)
    query.data = data
    update = MagicMock()
    update.callback_query = query
    update.effective_user = _USER
    return cast(Update, update)


def _advance_past_kickoff(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        game = GameRepository(session).get(_FID)
        assert game is not None
        game.kickoff_utc = _now_naive() - timedelta(hours=1)  # simulate kickoff having passed
        session.commit()


async def test_full_flow_fake_provider(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    provider = FakeProvider(fixtures=[_fixture()], results=[_result()])
    app_context = _app_context(settings, session_factory, provider, cap=100)

    # 1. Daily sync → game inserted + group announcement.
    job_ctx, bot = _job_context(app_context)
    await sync_job(job_ctx)
    bot.send_message.assert_awaited()  # announcement
    with session_factory() as session:
        assert GameRepository(session).get(_FID) is not None

    # 2. Bet via deep-link entry (auto-creates the player) → 3. place a WINNER:HOME bet.
    update, message = _start_update()
    start_ctx = MagicMock()
    start_ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    start_ctx.args = [f"bet_{_FID}"]
    await start_handler(update, cast(ContextTypes.DEFAULT_TYPE, start_ctx))
    message.reply_text.assert_awaited_once()

    cb_ctx = MagicMock()
    cb_ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    await on_callback(
        _cb_update(encode(WinnerInput(_FID, WinnerSel.HOME))),
        cast(ContextTypes.DEFAULT_TYPE, cb_ctx),
    )
    with session_factory() as session:
        assert len(BetRepository(session).list_for_game(_FID)) == 1

    # 4. Kickoff passes → live poll settles the game + posts results.
    _advance_past_kickoff(session_factory)
    poll_ctx, poll_bot = _job_context(app_context)
    await poll_job(poll_ctx)
    poll_bot.send_message.assert_awaited()  # results message to the group
    with session_factory() as session:
        game = GameRepository(session).get(_FID)
        assert game is not None and game.settled_at is not None
        bet = BetRepository(session).list_for_game(_FID)[0]
        assert bet.points_awarded == 2  # 2-1 → home win

    # 5. Scoreboard reflects the points.
    board_update, board_message = _start_update()
    board_ctx = MagicMock()
    board_ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    board_ctx.args = []
    await placar_handler(board_update, cast(ContextTypes.DEFAULT_TYPE, board_ctx))
    board_text = board_message.reply_text.await_args.args[0]
    assert "Alice" in board_text


async def test_budget_hard_stop_blocks_polling(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    provider = FakeProvider(fixtures=[_fixture()], results=[_result()])
    app_context = _app_context(settings, session_factory, provider, cap=1)  # only 1 request allowed

    # Sync consumes the single allowed request.
    job_ctx, _bot = _job_context(app_context)
    await sync_job(job_ctx)
    assert app_context.budget.is_exhausted() is True

    # Kickoff passes; polling must hard-stop (no get_live_results) and DM the admin.
    _advance_past_kickoff(session_factory)
    poll_ctx, poll_bot = _job_context(app_context)
    await poll_job(poll_ctx)

    assert provider.call_log == ["get_fixtures"]  # poll made NO provider call
    poll_bot.send_message.assert_awaited()  # admin cap alert
    assert poll_bot.send_message.await_args.kwargs["chat_id"] == settings.admin_user_id
    with session_factory() as session:
        game = GameRepository(session).get(_FID)
        assert game is not None and game.settled_at is None  # not settled (budget blocked)
