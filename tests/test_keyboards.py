"""Tests for inline keyboard builders (COMPLETION.md §8.2)."""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from tigrinho.bot.callbacks import (
    BttsInput,
    CallbackData,
    ChooseCategory,
    ChooseGame,
    ExactScore,
    HomeScore,
    ScorerInput,
    ScorerPage,
    WinnerInput,
    decode,
)
from tigrinho.bot.keyboards import (
    announcement_keyboard,
    away_score_keyboard,
    btts_keyboard,
    category_keyboard,
    deep_link_url,
    games_keyboard,
    home_score_keyboard,
    over_under_keyboard,
    squad_keyboard,
    winner_keyboard,
)
from tigrinho.domain.bets import WinnerSel
from tigrinho.enums import Stage


def _decoded(keyboard: InlineKeyboardMarkup) -> list[CallbackData]:
    return [
        decode(button.callback_data)
        for row in keyboard.inline_keyboard
        for button in row
        if isinstance(button.callback_data, str)
    ]


def test_deep_link_url() -> None:
    assert deep_link_url("TigrinhoDaCopaBot", 123) == "https://t.me/TigrinhoDaCopaBot?start=bet_123"


def test_announcement_keyboard_one_button_per_game() -> None:
    keyboard = announcement_keyboard(
        [(1, "Brasil x Argentina"), (2, "França x Alemanha")], "TigrinhoDaCopaBot"
    )
    assert len(keyboard.inline_keyboard) == 2
    button = keyboard.inline_keyboard[0][0]
    assert "Apostar" in button.text
    assert button.url == "https://t.me/TigrinhoDaCopaBot?start=bet_1"


def test_games_keyboard() -> None:
    assert _decoded(games_keyboard([(1001, "Brasil x Argentina")])) == [ChooseGame(1001)]


def test_category_keyboard_has_five_categories() -> None:
    categories = [d for d in _decoded(category_keyboard(1001)) if isinstance(d, ChooseCategory)]
    assert len(categories) == 5
    assert all(c.fixture_id == 1001 for c in categories)


def test_home_score_pad_has_zero_to_ten() -> None:
    values = sorted(
        d.value for d in _decoded(home_score_keyboard(1001)) if isinstance(d, HomeScore)
    )
    assert values == list(range(0, 11))


def test_away_score_pad_bakes_in_home() -> None:
    decoded = [d for d in _decoded(away_score_keyboard(1001, 2)) if isinstance(d, ExactScore)]
    assert sorted(d.away for d in decoded) == list(range(0, 11))
    assert all(d.home == 2 for d in decoded)


def test_winner_keyboard_hides_draw_for_knockout() -> None:
    group_sels = {
        d.sel
        for d in _decoded(winner_keyboard(1001, Stage.GROUP, "Brasil", "Argentina"))
        if isinstance(d, WinnerInput)
    }
    knockout_sels = {
        d.sel
        for d in _decoded(winner_keyboard(1001, Stage.KNOCKOUT, "Brasil", "Argentina"))
        if isinstance(d, WinnerInput)
    }
    assert WinnerSel.DRAW in group_sels
    assert knockout_sels == {WinnerSel.HOME, WinnerSel.AWAY}


def test_btts_and_over_under() -> None:
    btts = _decoded(btts_keyboard(1001))
    assert len(btts) == 4
    assert all(isinstance(d, BttsInput) for d in btts)
    assert len(_decoded(over_under_keyboard(1001))) == 2


def test_squad_keyboard_pagination() -> None:
    players = [(i, f"Player {i}") for i in range(20)]
    first = _decoded(squad_keyboard(1001, players, 0))
    assert sum(isinstance(d, ScorerInput) for d in first) == 8
    assert [d for d in first if isinstance(d, ScorerPage)] == [ScorerPage(1001, 1)]

    middle = {d for d in _decoded(squad_keyboard(1001, players, 1)) if isinstance(d, ScorerPage)}
    assert ScorerPage(1001, 0) in middle  # prev
    assert ScorerPage(1001, 2) in middle  # next
