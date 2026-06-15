"""Tests for /placar + the Geral↔Semana toggle (COMPLETION.md §10)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import CallbackQuery, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from tigrinho.bot.board_handlers import board_toggle, placar_handler
from tigrinho.bot.callbacks import BoardView, decode, encode
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.db.models import Game, GameStatus, Stage, utcnow
from tigrinho.db.repositories import BetRepository, PlayerRepository

_USER = User(id=42, is_bot=False, first_name="Alice")


def _seed_settled_bet(app_context: AppContext, *, points: int = 5) -> None:
    kickoff = datetime.now(app_context.settings.tzinfo).replace(tzinfo=None)
    with app_context.session_factory() as session:
        session.add(
            Game(
                fixture_id=1001,
                match_hash="h",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.FINISHED,
                settled_at=utcnow(),
            )
        )
        PlayerRepository(session).get_or_create(42, "Alice")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="EXACT_SCORE", payload_json="{}"
        )
        bet.points_awarded = points
        bet.is_correct = True
        bet.settled_at = utcnow()
        session.commit()


def _context(
    app_context: AppContext, *, args: list[str] | None = None
) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.args = args
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _cmd_update() -> tuple[Update, AsyncMock]:
    message = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = _USER
    return cast(Update, update), message


async def test_placar_default_geral(app_context: AppContext) -> None:
    _seed_settled_bet(app_context, points=5)
    update, message = _cmd_update()
    await placar_handler(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "Placar Geral" in text
    assert "Alice" in text
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(keyboard, InlineKeyboardMarkup)
    # the toggle offers Semana when showing Geral
    toggle_data = keyboard.inline_keyboard[0][0].callback_data
    assert isinstance(toggle_data, str)
    assert decode(toggle_data) == BoardView("semana")


async def test_placar_semana_argument(app_context: AppContext) -> None:
    _seed_settled_bet(app_context)
    update, message = _cmd_update()
    await placar_handler(update, _context(app_context, args=["semana"]))
    assert "Placar da Semana" in message.reply_text.await_args.args[0]


async def test_placar_empty(app_context: AppContext) -> None:
    update, message = _cmd_update()
    await placar_handler(update, _context(app_context))
    assert "Ainda não há pontos" in message.reply_text.await_args.args[0]


async def test_board_toggle_ignores_message_not_modified(app_context: AppContext) -> None:
    from telegram.error import BadRequest

    _seed_settled_bet(app_context)
    query = AsyncMock(spec=CallbackQuery)
    query.data = encode(BoardView("semana"))
    query.edit_message_text.side_effect = BadRequest("Message is not modified")
    update = MagicMock()
    update.callback_query = query
    update.effective_user = _USER
    await board_toggle(cast(Update, update), _context(app_context))  # must not raise


def _settled_game_with_bet(
    app_context: AppContext,
    *,
    fixture_id: int,
    kickoff_local: datetime,
    telegram_id: int,
    points: int,
) -> None:
    with app_context.session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="A",
                away_team_id=20,
                away_team_name="B",
                kickoff_utc=kickoff_local,
                kickoff_local=kickoff_local,
                status=GameStatus.FINISHED,
                settled_at=utcnow(),
            )
        )
        PlayerRepository(session).get_or_create(telegram_id, f"Player{telegram_id}")
        bet = BetRepository(session).upsert(
            fixture_id=fixture_id,
            player_telegram_id=telegram_id,
            category="WINNER",
            payload_json="{}",
        )
        bet.is_correct = points > 0
        bet.points_awarded = points
        bet.settled_at = utcnow()
        session.commit()


async def test_weekly_board_is_subset_of_geral(app_context: AppContext) -> None:
    now_local = datetime.now(app_context.settings.tzinfo).replace(tzinfo=None)
    # In-week game (2 pts) + last-month game (5 pts) for the same player.
    _settled_game_with_bet(
        app_context, fixture_id=1, kickoff_local=now_local, telegram_id=42, points=2
    )
    _settled_game_with_bet(
        app_context,
        fixture_id=2,
        kickoff_local=now_local - timedelta(days=30),
        telegram_id=42,
        points=5,
    )
    update, message = _cmd_update()
    await placar_handler(update, _context(app_context))  # Geral
    assert "<b>7</b> pts" in message.reply_text.await_args.args[0]  # 5 + 2

    update, message = _cmd_update()
    await placar_handler(update, _context(app_context, args=["semana"]))  # Semana
    weekly_text = message.reply_text.await_args.args[0]
    assert "<b>2</b> pts" in weekly_text  # only the in-week game
    assert "<b>7</b> pts" not in weekly_text


async def test_caller_outside_top_15_gets_own_line(app_context: AppContext) -> None:
    now_local = datetime.now(app_context.settings.tzinfo).replace(tzinfo=None)
    # 15 higher-ranked players + the caller (id 42) ranked 16th with the fewest points.
    for i in range(15):
        _settled_game_with_bet(
            app_context,
            fixture_id=100 + i,
            kickoff_local=now_local,
            telegram_id=200 + i,
            points=10 + i,
        )
    _settled_game_with_bet(
        app_context, fixture_id=1, kickoff_local=now_local, telegram_id=42, points=1
    )
    update, message = _cmd_update()
    await placar_handler(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "Você: 16º" in text


async def test_board_toggle_edits_message(app_context: AppContext) -> None:
    _seed_settled_bet(app_context)
    query = AsyncMock(spec=CallbackQuery)
    query.data = encode(BoardView("semana"))
    update = MagicMock()
    update.callback_query = query
    update.effective_user = _USER
    await board_toggle(cast(Update, update), _context(app_context))
    query.answer.assert_awaited()
    assert "Placar da Semana" in query.edit_message_text.await_args.args[0]
