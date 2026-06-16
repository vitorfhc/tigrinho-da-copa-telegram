"""Tests for inline keyboard builders (COMPLETION.md §8.2)."""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from tigrinho.bot.callbacks import (
    BttsInput,
    CallbackData,
    ChooseCategory,
    ChooseGame,
    ExactScore,
    FirstTeamInput,
    HomeScore,
    WinnerInput,
    decode,
)
from tigrinho.bot.keyboards import (
    announcement_keyboard,
    away_score_keyboard,
    btts_keyboard,
    category_keyboard,
    deep_link_url,
    first_team_keyboard,
    games_keyboard,
    home_score_keyboard,
    over_under_keyboard,
    winner_keyboard,
)
from tigrinho.domain.bets import FirstTeamSel, WinnerSel
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
    keyboard = btts_keyboard(1001, "Brasil", "Argentina")
    btts = _decoded(keyboard)
    assert len(btts) == 4
    assert all(isinstance(d, BttsInput) for d in btts)
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["Ambas marcam", "Só o Brasil", "Só o Argentina", "Nenhuma marca"]
    assert len(_decoded(over_under_keyboard(1001))) == 2


def test_first_team_keyboard() -> None:
    decoded = _decoded(first_team_keyboard(1001, "Brasil", "Argentina"))
    sels = {d.sel for d in decoded if isinstance(d, FirstTeamInput)}
    assert sels == {FirstTeamSel.HOME, FirstTeamSel.AWAY}
    keyboard = first_team_keyboard(1001, "Brasil", "Argentina")
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["Brasil", "Argentina"]
