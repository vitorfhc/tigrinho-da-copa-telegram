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
    HalfTimeResultInput,
    HomeScore,
    MyBetsHome,
    MyGameDetail,
    MyHistory,
    OverUnderInput,
    PalpiteView,
    SplitwiseInGroup,
    SplitwiseMemberPick,
    SplitwiseNotInGroup,
    SplitwiseRegisterPick,
    TournamentAction,
    TournamentAddToggle,
    TournamentCreateCancel,
    TournamentCreatePrice,
    WinnerInput,
    decode,
    encode,
)
from tigrinho.domain.bets import (
    BetCategory,
    BttsSel,
    FirstTeamSel,
    HalfTimeSel,
    OverUnderSel,
    WinnerSel,
)

_CASES: list[CallbackData] = [
    ChooseGame(123456),
    ChooseCategory(123456, BetCategory.EXACT_SCORE),
    ChooseCategory(123456, BetCategory.FIRST_TEAM),
    ChooseCategory(123456, BetCategory.BTTS),
    ChooseCategory(123456, BetCategory.WINNER),
    ChooseCategory(123456, BetCategory.OVER_UNDER),
    ChooseCategory(123456, BetCategory.HALF_TIME_RESULT),
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
    HalfTimeResultInput(123456, HalfTimeSel.HOME),
    HalfTimeResultInput(123456, HalfTimeSel.DRAW),
    HalfTimeResultInput(123456, HalfTimeSel.AWAY),
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
    MyHistory(0),
    MyHistory(5),
    MyGameDetail(123456, 0),
    MyGameDetail(999_999_999, 12),
    MyBetsHome(),
    TournamentAction("ba", 7),
    TournamentAction("bd", 7),
    TournamentAction("bo", 7),
    TournamentAction("bx", 7),
    TournamentAction("bj", 999_999_999),
    TournamentAction("bk", 999_999_999),
    TournamentAction("bi", 1),
    TournamentAction("bp", 42),
    TournamentAction("bs", 7),
    TournamentAddToggle(999_999_999, 999_999_999),
    TournamentCreatePrice(500),
    TournamentCreatePrice(123_456),
    TournamentCreatePrice(None),
    TournamentCreateCancel(),
    SplitwiseInGroup(),
    SplitwiseNotInGroup(),
    SplitwiseMemberPick(123_456_789),
    SplitwiseRegisterPick(42),
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
        "mh",
        "mh:x",
        "mg:1",
        "mg:1:x",
        "mg:x:0",
        "bc",
        "bc:",
        "bc:abc",
        "bc:1.5",
    ],
)
def test_decode_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        decode(bad)
