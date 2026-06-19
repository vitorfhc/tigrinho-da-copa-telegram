"""Tests for the Splitwise linking wizard + admin register handlers (§23)."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session, sessionmaker
from telegram import CallbackQuery, Chat, Update, User
from telegram.constants import ChatType
from telegram.ext import ContextTypes

import tigrinho.tournament_service as tsvc
from tigrinho.bot.callbacks import (
    SplitwiseInGroup,
    SplitwiseMemberPick,
    SplitwiseNotInGroup,
    SplitwiseRegisterPick,
    decode,
    encode,
)
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.bot.splitwise_handlers import (
    cmd_bolaozinho_splitwise,
    cmd_vincular_splitwise,
    on_splitwise_callback,
    on_splitwise_email_text,
)
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, SplitwiseMode, Stage, TournamentStatus
from tigrinho.db.repositories import BetRepository, PlayerRepository, TournamentRepository
from tigrinho.providers.splitwise import SplitwiseClient, SplitwiseMember, SplitwiseUser

_NOW = datetime(2026, 6, 16, 12, 0)
_USER = User(id=100, is_bot=False, first_name="Ana")
_ADMIN = User(id=999, is_bot=False, first_name="Admin")


def _enabled_ctx(
    settings: Settings, session_factory: sessionmaker[Session], client: object
) -> AppContext:
    return AppContext(
        settings=settings.model_copy(update={"splitwise_api_key": "k", "splitwise_group_id": 55}),
        provider=AsyncMock(),
        session_factory=session_factory,
        budget=AsyncMock(),
        splitwise_client=cast(SplitwiseClient, client),
    )


def _cmd_update(user: User, *, chat_type: ChatType = ChatType.PRIVATE) -> tuple[Update, AsyncMock]:
    message = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = user
    update.effective_chat = Chat(
        id=user.id if chat_type == ChatType.PRIVATE else -100, type=chat_type
    )
    return cast(Update, update), message


def _cb_update(data: str, user: User) -> tuple[Update, AsyncMock]:
    query = AsyncMock(spec=CallbackQuery)
    query.data = data
    update = MagicMock()
    update.callback_query = query
    update.effective_user = user
    return cast(Update, update), query


def _context(app_context: AppContext) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.bot = AsyncMock()
    ctx.user_data = {}
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _disabled_ctx(settings: Settings, session_factory: sessionmaker[Session]) -> AppContext:
    return AppContext(
        settings=settings,
        provider=AsyncMock(),
        session_factory=session_factory,
        budget=AsyncMock(),
        splitwise_client=None,
    )


async def test_vincular_disabled_reports(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _disabled_ctx(settings, session_factory)
    update, message = _cmd_update(_USER)
    await cmd_vincular_splitwise(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "não está configurado" in text


async def test_vincular_dm_shows_intro(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    update, message = _cmd_update(_USER, chat_type=ChatType.PRIVATE)
    await cmd_vincular_splitwise(update, _context(app_context))
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    decoded = [decode(b.callback_data) for row in keyboard.inline_keyboard for b in row]
    assert decoded == [SplitwiseInGroup(), SplitwiseNotInGroup()]


async def test_vincular_group_redirects_to_dm(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    update, message = _cmd_update(_USER, chat_type=ChatType.GROUP)
    await cmd_vincular_splitwise(update, _context(app_context))
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].url.endswith("?start=vincular")


async def test_in_group_picker_excludes_already_linked(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        p = PlayerRepository(session).get_or_create(100, "Ana")
        p.splitwise_user_id = 1001  # already linked to member 1001
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.get_group_members.return_value = [
        SplitwiseMember(id=1001, email="a@x", first_name="Ana", last_name=None),
        SplitwiseMember(id=1002, email="b@x", first_name="Bruno", last_name=None),
    ]
    app_context = _enabled_ctx(settings, session_factory, client)
    update, query = _cb_update(encode(SplitwiseInGroup()), _USER)
    await on_splitwise_callback(update, _context(app_context))
    keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
    decoded = [decode(b.callback_data) for row in keyboard.inline_keyboard for b in row]
    assert SplitwiseMemberPick(1002) in decoded
    assert SplitwiseMemberPick(1001) not in decoded


async def test_member_pick_stores_link(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    client = AsyncMock(spec=SplitwiseClient)
    client.get_group_members.return_value = [
        SplitwiseMember(id=1002, email="bruno@x.com", first_name="Bruno", last_name="Silva"),
    ]
    app_context = _enabled_ctx(settings, session_factory, client)
    update, query = _cb_update(encode(SplitwiseMemberPick(1002)), _USER)
    await on_splitwise_callback(update, _context(app_context))
    with session_factory() as session:
        player = PlayerRepository(session).get(100)
        assert player is not None
        assert player.splitwise_user_id == 1002
        assert player.splitwise_email == "bruno@x.com"


async def test_not_in_group_sets_await_flag(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    update, query = _cb_update(encode(SplitwiseNotInGroup()), _USER)
    ctx = _context(app_context)
    await on_splitwise_callback(update, ctx)
    assert ctx.user_data is not None and ctx.user_data["awaiting_splitwise_email"] is True


async def test_email_text_invalid_reprompts(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    message = AsyncMock()
    message.text = "not-an-email"
    update = MagicMock()
    update.effective_message = message
    update.effective_user = _USER
    ctx = _context(app_context)
    assert ctx.user_data is not None
    ctx.user_data["awaiting_splitwise_email"] = True
    await on_splitwise_email_text(cast(Update, update), ctx)
    assert "válido" in message.reply_text.await_args.args[0]
    assert ctx.user_data["awaiting_splitwise_email"] is True  # still waiting


async def test_email_text_valid_invites_and_stores(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    client = AsyncMock(spec=SplitwiseClient)
    client.add_user_to_group.return_value = SplitwiseUser(
        id=1234, email="ana@x.com", first_name="Ana", last_name=None
    )
    app_context = _enabled_ctx(settings, session_factory, client)
    message = AsyncMock()
    message.text = "ana@x.com"
    update = MagicMock()
    update.effective_message = message
    update.effective_user = _USER
    ctx = _context(app_context)
    assert ctx.user_data is not None
    ctx.user_data["awaiting_splitwise_email"] = True
    await on_splitwise_email_text(cast(Update, update), ctx)
    client.add_user_to_group.assert_awaited_once()
    with session_factory() as session:
        player = PlayerRepository(session).get(100)
        assert player is not None
        assert player.splitwise_user_id == 1234
        assert player.splitwise_email == "ana@x.com"


async def test_email_text_ignored_without_flag(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    message = AsyncMock()
    message.text = "ana@x.com"
    update = MagicMock()
    update.effective_message = message
    update.effective_user = _USER
    ctx = _context(app_context)  # user_data has no flag
    await on_splitwise_email_text(cast(Update, update), ctx)
    message.reply_text.assert_not_awaited()


def _seed_manual_finished(session: Session) -> int:
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    session.add(
        Game(
            fixture_id=1,
            match_hash="h1",
            stage=Stage.GROUP,
            home_team_id=10,
            home_team_name="Brasil",
            away_team_id=20,
            away_team_name="Argentina",
            kickoff_utc=datetime(2026, 6, 16, 19, 0),
            kickoff_local=datetime(2026, 6, 16, 19, 0),
            status=GameStatus.SCHEDULED,
        )
    )
    session.flush()
    t = tsvc.create_tournament(session, name="Fase", entry_price_cents=1000, created_by=1)
    tsvc.add_game(session, t, 1, now=_NOW)
    tsvc.open_tournament(session, t, now=_NOW)  # MANUAL
    for tid, uid in ((100, 1001), (200, 1002)):
        p = PlayerRepository(session).get(tid)
        assert p is not None
        p.splitwise_user_id = uid
    tsvc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    tsvc.join(session, t, telegram_id=200, display_name="Bruno", now=_NOW)
    for player, points in ((100, 5), (200, 2)):
        bet = BetRepository(session).upsert(
            fixture_id=1, player_telegram_id=player, category="WINNER", payload_json="{}"
        )
        bet.is_correct = points > 0
        bet.points_awarded = points
        bet.settled_at = datetime(2026, 6, 16, 21, 0)
    game = session.get(Game, 1)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.settled_at = datetime(2026, 6, 16, 21, 0)
    t.status = TournamentStatus.FINISHED
    session.flush()
    return t.id


async def test_admin_command_rejects_non_admin(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    update, message = _cmd_update(_USER)  # id 100, not the admin (999)
    await cmd_bolaozinho_splitwise(update, _context(app_context))
    assert "admin" in message.reply_text.await_args.args[0].lower()


async def test_admin_command_lists_ready(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_manual_finished(session)
        session.commit()
    app_context = _enabled_ctx(settings, session_factory, AsyncMock(spec=SplitwiseClient))
    update, message = _cmd_update(_ADMIN)
    await cmd_bolaozinho_splitwise(update, _context(app_context))
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    decoded = [decode(b.callback_data) for row in keyboard.inline_keyboard for b in row]
    assert SplitwiseRegisterPick(tid) in decoded


async def test_admin_register_pick_registers(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_manual_finished(session)
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.create_expense.return_value = 888
    app_context = _enabled_ctx(settings, session_factory, client)
    update, query = _cb_update(encode(SplitwiseRegisterPick(tid)), _ADMIN)
    await on_splitwise_callback(update, _context(app_context))
    client.create_expense.assert_awaited_once()
    with session_factory() as session:
        t = TournamentRepository(session).get(tid)
        assert t is not None and t.splitwise_expense_id == 888
        assert t.splitwise_mode is SplitwiseMode.MANUAL  # mode unchanged; just registered
