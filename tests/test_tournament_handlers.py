"""Tests for the bolãozinho handlers (Feature 7 / §22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import CallbackQuery, Chat, InlineKeyboardMarkup, Update, User
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from tigrinho import tournament_service as svc
from tigrinho.bot.callbacks import TournamentAction, TournamentAddToggle, encode
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.bot.tournament_handlers import (
    cmd_abrir,
    cmd_criar,
    cmd_entrar,
    on_tournament_callback,
)
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import TournamentRepository

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


def _cmd_update(user: User) -> tuple[Update, AsyncMock]:
    message = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = user
    update.effective_chat = Chat(id=-100, type=ChatType.GROUP)
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


async def test_entrar_and_join_flow(app_context: AppContext) -> None:
    _seed_game(app_context, 1001)
    tid = _make_tournament(app_context, opened=True)
    # /entrar shows the join card (single joinable tournament).
    update, message = _cmd_update(_OTHER)
    await cmd_entrar(update, _context(app_context))
    assert isinstance(message.reply_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)
    # Tapping "Entrar" creates the entry.
    update2, query = _cb_update(encode(TournamentAction("bk", tid)), _OTHER)
    await on_tournament_callback(update2, _context(app_context))
    with app_context.session_factory() as session:
        assert TournamentRepository(session).is_entered(tid, 8) is True


async def test_entrar_none_open(app_context: AppContext) -> None:
    update, message = _cmd_update(_OTHER)
    await cmd_entrar(update, _context(app_context))
    assert "Nenhum bolãozinho" in message.reply_text.await_args.args[0]
