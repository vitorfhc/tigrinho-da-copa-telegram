"""Tests for the callback_data codec (COMPLETION.md §3, §8.2, §16)."""

from __future__ import annotations

import pytest

from tigrinho.bot.callbacks import (
    MAX_CALLBACK_BYTES,
    BoardView,
    BttsInput,
    CallbackData,
    Cancel,
    ChooseCategory,
    ChooseGame,
    DeleteBet,
    ExactScore,
    FirstTeamInput,
    GameBoard,
    GamesBoardCompute,
    GamesBoardToggle,
    HomeScore,
    OverUnderInput,
    PalpiteView,
    WinnerInput,
    decode,
    encode,
)
from tigrinho.domain.bets import BetCategory, BttsSel, FirstTeamSel, OverUnderSel, WinnerSel

_CASES: list[CallbackData] = [
    ChooseGame(123456),
    ChooseCategory(123456, BetCategory.EXACT_SCORE),
    ChooseCategory(123456, BetCategory.FIRST_TEAM),
    ChooseCategory(123456, BetCategory.BTTS),
    ChooseCategory(123456, BetCategory.WINNER),
    ChooseCategory(123456, BetCategory.OVER_UNDER),
    HomeScore(123456, 3),
    ExactScore(123456, 2, 10),
    WinnerInput(123456, WinnerSel.HOME),
    WinnerInput(123456, WinnerSel.DRAW),
    WinnerInput(123456, WinnerSel.AWAY),
    BttsInput(123456, BttsSel.BOTH),
    BttsInput(123456, BttsSel.ONLY_HOME),
    BttsInput(123456, BttsSel.ONLY_AWAY),
    BttsInput(123456, BttsSel.NEITHER),
    OverUnderInput(123456, OverUnderSel.OVER),
    OverUnderInput(123456, OverUnderSel.UNDER),
    FirstTeamInput(123456, FirstTeamSel.HOME),
    FirstTeamInput(123456, FirstTeamSel.AWAY),
    DeleteBet(42),
    Cancel(),
    BoardView("geral"),
    BoardView("semana"),
    GameBoard(123456),
    GamesBoardToggle(0, 0),
    GamesBoardToggle(1023, 9),
    GamesBoardCompute(0),
    GamesBoardCompute(1023),
    PalpiteView(123456),
]


@pytest.mark.parametrize("original", _CASES)
def test_round_trip(original: CallbackData) -> None:
    encoded = encode(original)
    assert decode(encoded) == original


@pytest.mark.parametrize("original", _CASES)
def test_within_64_bytes(original: CallbackData) -> None:
    assert len(encode(original).encode("utf-8")) <= MAX_CALLBACK_BYTES


def test_within_64_bytes_for_large_ids() -> None:
    # API-Football fixture ids are small, but verify headroom for 9-digit ids.
    assert len(encode(ExactScore(999_999_999, 99, 99)).encode("utf-8")) <= MAX_CALLBACK_BYTES


def test_encode_rejects_oversized() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        encode(ExactScore(10**60, 10**60, 10**60))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "z:1",
        "g:notanint",
        "c:1:Z",
        "s:1:x",
        "w:1:Q",
        "f:1",
        "f:1:Z",
        "g",
        "gb",
        "gb:x",
        "pjt:1",
        "pjt:x:0",
        "pjt:1:x",
        "pjc:x",
        "pv",
        "pv:x",
    ],
)
def test_decode_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        decode(bad)
