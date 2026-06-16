"""Tests for the betting handlers/wizard (COMPLETION.md §8.2, §16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import CallbackQuery, Chat, InlineKeyboardMarkup, Update, User
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from tigrinho.bot.bets_handlers import (
    apostar_handler,
    jogos_handler,
    minhas_apostas_handler,
    on_callback,
    start_handler,
)
from tigrinho.bot.callbacks import (
    BttsInput,
    ChooseCategory,
    ChooseGame,
    DeleteBet,
    ExactScore,
    FirstTeamInput,
    HomeScore,
    OverUnderInput,
    WinnerInput,
    decode,
    encode,
)
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.db.models import Bet, Game, GameStatus, Stage
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.domain.bets import BetCategory, BttsSel, FirstTeamSel, OverUnderSel, WinnerSel
from tigrinho.domain.text_pt import format_kickoff_short

_USER = User(id=42, is_bot=False, first_name="Tigrão")


def _seed_game(
    app_context: AppContext,
    *,
    fixture_id: int = 1001,
    started: bool = False,
    stage: Stage = Stage.GROUP,
) -> None:
    kickoff = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(hours=-2 if started else 2)
    with app_context.session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash="h",
                stage=stage,
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


def _context(
    app_context: AppContext, *, args: list[str] | None = None
) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.args = args
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _cmd_update(*, chat_type: ChatType = ChatType.PRIVATE) -> tuple[Update, AsyncMock]:
    message = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = _USER
    update.effective_chat = Chat(id=1 if chat_type == ChatType.PRIVATE else -100, type=chat_type)
    return cast(Update, update), message


def _cb_update(data: str) -> tuple[Update, AsyncMock]:
    query = AsyncMock(spec=CallbackQuery)
    query.data = data
    update = MagicMock()
    update.callback_query = query
    update.effective_user = _USER
    return cast(Update, update), query


def _bets(app_context: AppContext, fixture_id: int = 1001) -> list[Bet]:
    with app_context.session_factory() as session:
        return BetRepository(session).list_for_game(fixture_id)


# --- deep-link entry ------------------------------------------------------------------------


async def test_start_deep_link_creates_player_and_opens_category(app_context: AppContext) -> None:
    _seed_game(app_context)
    update, message = _cmd_update()
    await start_handler(update, _context(app_context, args=["bet_1001"]))
    message.reply_text.assert_awaited_once()
    assert isinstance(message.reply_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)
    with app_context.session_factory() as session:
        assert PlayerRepository(session).get(42) is not None


async def test_start_no_payload_shows_welcome(app_context: AppContext) -> None:
    update, message = _cmd_update()
    await start_handler(update, _context(app_context, args=[]))
    assert "Bem-vindo" in message.reply_text.await_args.args[0]


async def test_start_bad_payload_shows_welcome(app_context: AppContext) -> None:
    update, message = _cmd_update()
    await start_handler(update, _context(app_context, args=["bet_notanint"]))
    assert "Bem-vindo" in message.reply_text.await_args.args[0]


# --- /apostar -------------------------------------------------------------------------------


async def test_apostar_dm_lists_open_games(app_context: AppContext) -> None:
    _seed_game(app_context)
    update, message = _cmd_update(chat_type=ChatType.PRIVATE)
    await apostar_handler(update, _context(app_context))
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    with app_context.session_factory() as session:
        game = GameRepository(session).get(1001)
    assert game is not None
    kickoff_local = game.kickoff_local
    label = markup.inline_keyboard[0][0].text
    assert label == f"Brasil x Argentina · {format_kickoff_short(kickoff_local)}"


async def test_apostar_group_redirects_to_private(app_context: AppContext) -> None:
    update, message = _cmd_update(chat_type=ChatType.SUPERGROUP)
    await apostar_handler(update, _context(app_context))
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    url = markup.inline_keyboard[0][0].url
    assert url is not None and "start=apostar" in url


async def test_start_apostar_payload_opens_games_picker(app_context: AppContext) -> None:
    # The group "Apostar no privado" deep link (?start=apostar) must open the games picker,
    # not the welcome message.
    _seed_game(app_context)
    update, message = _cmd_update()
    await start_handler(update, _context(app_context, args=["apostar"]))
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert any(
        isinstance(decode(b.callback_data), ChooseGame)
        for row in markup.inline_keyboard
        for b in row
        if isinstance(b.callback_data, str)
    )


# --- wizard transitions ---------------------------------------------------------------------


async def test_choose_exact_score_shows_home_pad(app_context: AppContext) -> None:
    _seed_game(app_context)
    update, query = _cb_update(encode(ChooseCategory(1001, BetCategory.EXACT_SCORE)))
    await on_callback(update, _context(app_context))
    query.answer.assert_awaited()
    assert isinstance(
        query.edit_message_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup
    )


async def test_home_then_away_score_finalizes_bet(app_context: AppContext) -> None:
    _seed_game(app_context)
    # home digit -> away pad
    update, query = _cb_update(encode(HomeScore(1001, 2)))
    await on_callback(update, _context(app_context))
    assert query.edit_message_text.await_args is not None
    # finalize exact score
    update, query = _cb_update(encode(ExactScore(1001, 2, 1)))
    await on_callback(update, _context(app_context))
    bets = _bets(app_context)
    assert len(bets) == 1
    assert bets[0].category == BetCategory.EXACT_SCORE.value
    assert bets[0].payload_json == '{"home":2,"away":1}'


async def test_winner_finalizes_bet(app_context: AppContext) -> None:
    _seed_game(app_context)
    update, query = _cb_update(encode(WinnerInput(1001, WinnerSel.HOME)))
    await on_callback(update, _context(app_context))
    bets = _bets(app_context)
    assert len(bets) == 1
    assert bets[0].category == BetCategory.WINNER.value


async def test_time_based_closing_rejects_bet(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)
    update, query = _cb_update(encode(WinnerInput(1001, WinnerSel.HOME)))
    await on_callback(update, _context(app_context))
    assert "fechad" in query.edit_message_text.await_args.args[0]
    assert _bets(app_context) == []


# --- /minhas_apostas + delete ---------------------------------------------------------------


async def test_minhas_apostas_and_delete(app_context: AppContext) -> None:
    _seed_game(app_context)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        bet_id = bet.id
        session.commit()

    update, message = _cmd_update()
    await minhas_apostas_handler(update, _context(app_context))
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)

    update, query = _cb_update(encode(DeleteBet(bet_id)))
    await on_callback(update, _context(app_context))
    assert _bets(app_context) == []


async def test_btts_and_over_under_finalize(app_context: AppContext) -> None:
    _seed_game(app_context)
    await on_callback(_cb_update(encode(BttsInput(1001, BttsSel.BOTH)))[0], _context(app_context))
    await on_callback(
        _cb_update(encode(OverUnderInput(1001, OverUnderSel.OVER)))[0], _context(app_context)
    )
    categories = {b.category for b in _bets(app_context)}
    assert categories == {"BTTS", "OVER_UNDER"}


async def test_first_team_step_and_finalize(app_context: AppContext) -> None:
    _seed_game(app_context)  # no squads needed anymore
    # category step shows the two-team keyboard
    update, query = _cb_update(encode(ChooseCategory(1001, BetCategory.FIRST_TEAM)))
    await on_callback(update, _context(app_context))
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    decoded = [
        decode(b.callback_data)
        for row in markup.inline_keyboard
        for b in row
        if isinstance(b.callback_data, str)
    ]
    sels = {d.sel for d in decoded if isinstance(d, FirstTeamInput)}
    assert sels == {FirstTeamSel.HOME, FirstTeamSel.AWAY}
    # picking a team finalizes the bet
    await on_callback(
        _cb_update(encode(FirstTeamInput(1001, FirstTeamSel.HOME)))[0], _context(app_context)
    )
    bets = _bets(app_context)
    assert len(bets) == 1
    assert bets[0].category == "FIRST_TEAM"


async def test_delete_other_players_bet_is_rejected(app_context: AppContext) -> None:
    _seed_game(app_context)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(99, "Someone Else")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=99, category="WINNER", payload_json="{}"
        )
        bet_id = bet.id
        session.commit()
    # _USER is id 42, not the owner (99)
    update, query = _cb_update(encode(DeleteBet(bet_id)))
    await on_callback(update, _context(app_context))
    assert "não encontrado" in query.edit_message_text.await_args.args[0]
    assert len(_bets(app_context)) == 1  # not deleted


async def test_delete_on_started_game_is_rejected(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json="{}"
        )
        bet_id = bet.id
        session.commit()
    update, query = _cb_update(encode(DeleteBet(bet_id)))
    await on_callback(update, _context(app_context))
    assert "fechad" in query.edit_message_text.await_args.args[0]
    assert len(_bets(app_context)) == 1  # not deleted


async def test_minhas_apostas_renders_settled_bet(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)  # not open -> settled section
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        bet.is_correct = True
        bet.points_awarded = 2
        bet.settled_at = datetime.now(tz=UTC).replace(tzinfo=None)
        session.commit()
    update, message = _cmd_update()
    await minhas_apostas_handler(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "Encerrados" in text
    assert "2 pts" in text and "✓" in text


async def test_minhas_apostas_shows_started_ungraded_bet_as_pending(
    app_context: AppContext,
) -> None:
    # A game that kicked off but is not yet settled must NOT render as a definitive loss
    # (✗, 0 pts); it belongs in a neutral "in progress / awaiting result" bucket (§8.2).
    _seed_game(app_context, started=True)  # not open, but the bet is left ungraded
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        session.commit()  # is_correct / points_awarded / settled_at all remain None
    update, message = _cmd_update()
    await minhas_apostas_handler(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "Encerrados" not in text
    assert "✗" not in text and "0 pts" not in text
    assert "andamento" in text.lower() or "aguardando" in text.lower()


# --- /jogos ---------------------------------------------------------------------------------


async def test_jogos_lists_games(app_context: AppContext) -> None:
    _seed_game(app_context)
    update, message = _cmd_update()
    await jogos_handler(update, _context(app_context))
    assert "Próximos jogos" in message.reply_text.await_args.args[0]


async def test_jogos_empty(app_context: AppContext) -> None:
    update, message = _cmd_update()
    await jogos_handler(update, _context(app_context))
    assert "Não há jogos" in message.reply_text.await_args.args[0]
