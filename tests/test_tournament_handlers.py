"""Tests for the bolãozinho handlers (Feature 7 / §22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import CallbackQuery, Chat, InlineKeyboardMarkup, Update, User
from telegram.constants import ChatType
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from tigrinho import tournament_service as svc
from tigrinho.bot.callbacks import TournamentAction, TournamentAddToggle, encode
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.bot.tournament_handlers import (
    cmd_abrir,
    cmd_cancelar,
    cmd_criar,
    cmd_entrar,
    cmd_participantes,
    on_tournament_callback,
    show_join_card_dm,
)
from tigrinho.db.models import Game, GameStatus, Stage, TournamentStatus
from tigrinho.db.repositories import PlayerRepository, TournamentRepository

_CREATOR = User(id=7, is_bot=False, first_name="Dono")
_OTHER = User(id=8, is_bot=False, first_name="Outro")


def _seed_game(app_context: AppContext, fixture_id: int) -> None:
    kickoff = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(hours=3)
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


def _cmd_update(user: User, *, chat_type: ChatType = ChatType.GROUP) -> tuple[Update, AsyncMock]:
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


def _context(
    app_context: AppContext, *, args: list[str] | None = None
) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.args = args
    ctx.bot = AsyncMock()
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _make_tournament(app_context: AppContext, *, created_by: int = 7, opened: bool = False) -> int:
    with app_context.session_factory() as session:
        tournament = svc.create_tournament(
            session, name="Oitavas", entry_price_cents=1000, created_by=created_by
        )
        svc.add_game(session, tournament, 1001, now=datetime.now(tz=UTC).replace(tzinfo=None))
        if opened:
            svc.open_tournament(session, tournament, now=datetime.now(tz=UTC).replace(tzinfo=None))
        session.commit()
        return tournament.id


async def test_criar_creates_draft_with_card(app_context: AppContext) -> None:
    update, message = _cmd_update(_CREATOR)
    await cmd_criar(update, _context(app_context, args=["Oitavas", "de", "final", "|", "10"]))
    message.reply_text.assert_awaited_once()
    assert isinstance(message.reply_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)
    with app_context.session_factory() as session:
        tournaments = TournamentRepository(session).list_all()
    assert len(tournaments) == 1
    assert tournaments[0].name == "Oitavas de final"
    assert tournaments[0].entry_price_cents == 1000
    assert tournaments[0].created_by == 7


async def test_criar_bad_usage(app_context: AppContext) -> None:
    update, message = _cmd_update(_CREATOR)
    await cmd_criar(update, _context(app_context, args=["semprice"]))
    assert "Uso" in message.reply_text.await_args.args[0]
    with app_context.session_factory() as session:
        assert TournamentRepository(session).list_all() == []


async def test_toggle_adds_game_for_creator(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    with app_context.session_factory() as session:
        tournament = svc.create_tournament(session, name="T", entry_price_cents=1000, created_by=7)
        session.commit()
        tid = tournament.id
    update, query = _cb_update(encode(TournamentAddToggle(tid, 1001)), _CREATOR)
    await on_tournament_callback(update, _context(app_context))
    with app_context.session_factory() as session:
        assert TournamentRepository(session).list_game_ids(tid) == [1001]


async def test_toggle_refused_for_non_creator(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    with app_context.session_factory() as session:
        tournament = svc.create_tournament(session, name="T", entry_price_cents=1000, created_by=7)
        session.commit()
        tid = tournament.id
    update, query = _cb_update(encode(TournamentAddToggle(tid, 1001)), _OTHER)
    await on_tournament_callback(update, _context(app_context))
    query.answer.assert_awaited_with("Só quem criou o bolãozinho pode mexer nele.", show_alert=True)
    with app_context.session_factory() as session:
        assert TournamentRepository(session).list_game_ids(tid) == []


async def test_abrir_opens_and_announces_to_group(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=False)
    update, message = _cmd_update(_CREATOR)
    ctx = _context(app_context, args=[str(tid)])
    await cmd_abrir(update, ctx)
    ctx.bot.send_message.assert_awaited_once()  # type: ignore[attr-defined]
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
    assert tournament is not None
    assert tournament.status.value == "OPEN"


async def test_entrar_in_dm_shows_join_card_and_join_flow(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    # /entrar in DM shows the open-bolãozinhos picker (a wizard step).
    update, message = _cmd_update(_OTHER, chat_type=ChatType.PRIVATE)
    await cmd_entrar(update, _context(app_context))
    assert isinstance(message.reply_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)
    # Tapping "Entrar" (after picking the bolãozinho) creates the entry.
    update2, query = _cb_update(encode(TournamentAction("bk", tid)), _OTHER)
    await on_tournament_callback(update2, _context(app_context))
    with app_context.session_factory() as session:
        assert TournamentRepository(session).is_entered(tid, 8) is True


async def test_entrar_in_group_redirects_to_private(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    _make_tournament(app_context, opened=True)
    update, message = _cmd_update(_OTHER, chat_type=ChatType.GROUP)
    await cmd_entrar(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "privado" in text
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url.endswith("?start=entrar")


async def test_entrar_none_open(app_context: AppContext) -> None:
    update, message = _cmd_update(_OTHER, chat_type=ChatType.PRIVATE)
    await cmd_entrar(update, _context(app_context))
    assert "Nenhum bolãozinho" in message.reply_text.await_args.args[0]


async def test_join_sends_confirmation_to_dm(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    update, query = _cb_update(encode(TournamentAction("bk", tid)), _OTHER)
    ctx = _context(app_context)
    await on_tournament_callback(update, ctx)
    # The confirmation (with bet deep-links) is DM'd to the joining user, not posted in the group.
    ctx.bot.send_message.assert_awaited_once()  # type: ignore[attr-defined]
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == 8  # type: ignore[attr-defined]
    with app_context.session_factory() as session:
        assert TournamentRepository(session).is_entered(tid, 8) is True


async def test_join_dm_failure_still_records_entry_and_alerts(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    update, query = _cb_update(encode(TournamentAction("bk", tid)), _OTHER)
    ctx = _context(app_context)
    ctx.bot.send_message.side_effect = Forbidden("bot can't initiate")  # type: ignore[attr-defined]
    await on_tournament_callback(update, ctx)
    # Entry is committed before the DM, so it survives a DM failure; the user gets an alert.
    with app_context.session_factory() as session:
        assert TournamentRepository(session).is_entered(tid, 8) is True
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_participantes_lists_entrants(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        svc.join(session, tournament, telegram_id=100, display_name="Ana", now=now)
        svc.join(session, tournament, telegram_id=200, display_name="Bruno", now=now)
        session.commit()
    update, message = _cmd_update(_OTHER)
    await cmd_participantes(update, _context(app_context, args=[str(tid)]))
    text = message.reply_text.await_args.args[0]
    assert "Participantes" in text
    assert "Ana" in text
    assert "Bruno" in text


async def test_participantes_no_arg_none_exist(app_context: AppContext) -> None:
    update, message = _cmd_update(_OTHER)
    await cmd_participantes(update, _context(app_context, args=[]))
    assert "Nenhum bolãozinho" in message.reply_text.await_args.args[0]


async def test_participantes_no_arg_shows_picker(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    _seed_game(app_context, 1002)
    with app_context.session_factory() as session:
        svc.create_tournament(session, name="A", entry_price_cents=1000, created_by=7)
        svc.create_tournament(session, name="B", entry_price_cents=2000, created_by=7)
        session.commit()
    update, message = _cmd_update(_OTHER)
    await cmd_participantes(update, _context(app_context, args=[]))
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 2  # one picker button per bolãozinho


async def test_participantes_callback_shows_list(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        svc.join(session, tournament, telegram_id=100, display_name="Ana", now=now)
        session.commit()
    update, query = _cb_update(encode(TournamentAction("bp", tid)), _OTHER)
    await on_tournament_callback(update, _context(app_context))
    text = query.edit_message_text.await_args.args[0]
    assert "Participantes" in text
    assert "Ana" in text


async def test_open_announcement_mentions_known_players(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=False)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(555, "Zé")
        session.commit()
    update, message = _cmd_update(_CREATOR)
    ctx = _context(app_context, args=[str(tid)])
    await cmd_abrir(update, ctx)
    text = ctx.bot.send_message.await_args.kwargs["text"]  # type: ignore[attr-defined]
    assert "tg://user?id=555" in text  # the known player is pinged in the announcement


async def test_open_announcement_has_entrar_button(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=False)
    update, message = _cmd_update(_CREATOR)
    ctx = _context(app_context, args=[str(tid)])
    await cmd_abrir(update, ctx)
    markup = ctx.bot.send_message.await_args.kwargs["reply_markup"]  # type: ignore[attr-defined]
    # Single Entrar deep-link button (no per-game bet buttons).
    assert markup.inline_keyboard[0][0].url.endswith(f"?start=entrar_{tid}")


async def test_entrar_deep_link_shows_join_card(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    update, message = _cmd_update(_OTHER, chat_type=ChatType.PRIVATE)
    await show_join_card_dm(update, app_context, tid)
    assert isinstance(message.reply_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)


async def test_cancel_command_notifies_entrants_with_reason(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)  # created_by=7 (_CREATOR)
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        svc.join(session, tournament, telegram_id=100, display_name="Ana", now=now)
        svc.join(session, tournament, telegram_id=200, display_name="Bruno", now=now)
        session.commit()
    update, message = _cmd_update(_CREATOR)
    ctx = _context(app_context, args=[str(tid), "jogo", "adiado"])
    await cmd_cancelar(update, ctx)
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        assert tournament.status is TournamentStatus.CANCELLED
        assert tournament.cancel_reason == "jogo adiado"
    # Both entrants were DM'd the cancellation + reason.
    assert ctx.bot.send_message.await_count == 2  # type: ignore[attr-defined]
    dm_ids = {c.kwargs["chat_id"] for c in ctx.bot.send_message.await_args_list}  # type: ignore[attr-defined]
    assert dm_ids == {100, 200}
    dm_text = ctx.bot.send_message.await_args_list[0].kwargs["text"]  # type: ignore[attr-defined]
    assert "cancelado" in dm_text
    assert "jogo adiado" in dm_text


async def test_cancel_command_refused_for_non_creator(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)  # created_by=7
    update, message = _cmd_update(_OTHER)  # id 8
    await cmd_cancelar(update, _context(app_context, args=[str(tid)]))
    assert "Só quem criou" in message.reply_text.await_args.args[0]
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tid)
        assert tournament is not None
        assert tournament.status is TournamentStatus.OPEN
