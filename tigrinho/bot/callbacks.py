"""Compact ``callback_data`` codec (COMPLETION.md §3, §8.2).

Telegram limits inline-button ``callback_data`` to **64 bytes**, so wizard state is packed as
short colon-delimited opcodes carrying only numeric ids + 1-char selectors (never human-readable
payloads). :func:`encode` round-trips with :func:`decode`.

Opcodes:
  ``g:<fixture>``                     choose game (open the category step)
  ``c:<fixture>:<E|F|B|W|O>``         choose category
  ``s:<fixture>:<h|a>:<0-10>``        exact-score digit for a side
  ``w:<fixture>:<H|D|A>``             winner selection
  ``t:<fixture>:<B|H|A|N>``           both-teams-to-score selection
  ``o:<fixture>:<O|U>``               over/under selection
  ``p:<fixture>:<page>``              paginate the first-scorer squad keyboard
  ``f:<fixture>:<player_id>``         first-scorer selection
  ``x:<bet_id>``                      delete a bet
  ``q``                               cancel/close the wizard
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never, cast

from tigrinho.domain.bets import BetCategory, BttsSel, OverUnderSel, WinnerSel

MAX_CALLBACK_BYTES = 64

Side = Literal["h", "a"]

_CATEGORY_TO_CODE: dict[BetCategory, str] = {
    BetCategory.EXACT_SCORE: "E",
    BetCategory.FIRST_SCORER: "F",
    BetCategory.BTTS: "B",
    BetCategory.WINNER: "W",
    BetCategory.OVER_UNDER: "O",
}
_CODE_TO_CATEGORY = {code: category for category, code in _CATEGORY_TO_CODE.items()}

_WINNER_TO_CODE: dict[WinnerSel, str] = {
    WinnerSel.HOME: "H",
    WinnerSel.DRAW: "D",
    WinnerSel.AWAY: "A",
}
_CODE_TO_WINNER = {code: sel for sel, code in _WINNER_TO_CODE.items()}

_BTTS_TO_CODE: dict[BttsSel, str] = {
    BttsSel.BOTH: "B",
    BttsSel.ONLY_HOME: "H",
    BttsSel.ONLY_AWAY: "A",
    BttsSel.NEITHER: "N",
}
_CODE_TO_BTTS = {code: sel for sel, code in _BTTS_TO_CODE.items()}

_OVER_UNDER_TO_CODE: dict[OverUnderSel, str] = {
    OverUnderSel.OVER: "O",
    OverUnderSel.UNDER: "U",
}
_CODE_TO_OVER_UNDER = {code: sel for sel, code in _OVER_UNDER_TO_CODE.items()}


@dataclass(frozen=True, slots=True)
class ChooseGame:
    fixture_id: int


@dataclass(frozen=True, slots=True)
class ChooseCategory:
    fixture_id: int
    category: BetCategory


@dataclass(frozen=True, slots=True)
class ScoreInput:
    fixture_id: int
    side: Side
    value: int


@dataclass(frozen=True, slots=True)
class WinnerInput:
    fixture_id: int
    sel: WinnerSel


@dataclass(frozen=True, slots=True)
class BttsInput:
    fixture_id: int
    sel: BttsSel


@dataclass(frozen=True, slots=True)
class OverUnderInput:
    fixture_id: int
    sel: OverUnderSel


@dataclass(frozen=True, slots=True)
class ScorerPage:
    fixture_id: int
    page: int


@dataclass(frozen=True, slots=True)
class ScorerInput:
    fixture_id: int
    player_id: int


@dataclass(frozen=True, slots=True)
class DeleteBet:
    bet_id: int


@dataclass(frozen=True, slots=True)
class Cancel:
    pass


CallbackData = (
    ChooseGame
    | ChooseCategory
    | ScoreInput
    | WinnerInput
    | BttsInput
    | OverUnderInput
    | ScorerPage
    | ScorerInput
    | DeleteBet
    | Cancel
)


def encode(data: CallbackData) -> str:
    """Encode wizard state to a ``callback_data`` string (≤ 64 bytes)."""
    match data:
        case ChooseGame(fixture_id):
            result = f"g:{fixture_id}"
        case ChooseCategory(fixture_id, category):
            result = f"c:{fixture_id}:{_CATEGORY_TO_CODE[category]}"
        case ScoreInput(fixture_id, side, value):
            result = f"s:{fixture_id}:{side}:{value}"
        case WinnerInput(fixture_id, sel):
            result = f"w:{fixture_id}:{_WINNER_TO_CODE[sel]}"
        case BttsInput(fixture_id, sel):
            result = f"t:{fixture_id}:{_BTTS_TO_CODE[sel]}"
        case OverUnderInput(fixture_id, sel):
            result = f"o:{fixture_id}:{_OVER_UNDER_TO_CODE[sel]}"
        case ScorerPage(fixture_id, page):
            result = f"p:{fixture_id}:{page}"
        case ScorerInput(fixture_id, player_id):
            result = f"f:{fixture_id}:{player_id}"
        case DeleteBet(bet_id):
            result = f"x:{bet_id}"
        case Cancel():
            result = "q"
        case _:  # pragma: no cover - exhaustiveness guard
            assert_never(data)
    if len(result.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data exceeds {MAX_CALLBACK_BYTES} bytes: {result!r}")
    return result


def _side(value: str) -> Side:
    if value not in ("h", "a"):
        raise ValueError(f"invalid score side: {value!r}")
    return cast(Side, value)


def decode(data: str) -> CallbackData:
    """Parse a ``callback_data`` string back to typed wizard state (raises on malformed)."""
    parts = data.split(":")
    op = parts[0]
    try:
        if op == "g":
            return ChooseGame(int(parts[1]))
        if op == "c":
            return ChooseCategory(int(parts[1]), _CODE_TO_CATEGORY[parts[2]])
        if op == "s":
            return ScoreInput(int(parts[1]), _side(parts[2]), int(parts[3]))
        if op == "w":
            return WinnerInput(int(parts[1]), _CODE_TO_WINNER[parts[2]])
        if op == "t":
            return BttsInput(int(parts[1]), _CODE_TO_BTTS[parts[2]])
        if op == "o":
            return OverUnderInput(int(parts[1]), _CODE_TO_OVER_UNDER[parts[2]])
        if op == "p":
            return ScorerPage(int(parts[1]), int(parts[2]))
        if op == "f":
            return ScorerInput(int(parts[1]), int(parts[2]))
        if op == "x":
            return DeleteBet(int(parts[1]))
        if op == "q":
            return Cancel()
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"invalid callback_data: {data!r}") from exc
    raise ValueError(f"unknown callback_data opcode: {data!r}")
