"""Compact ``callback_data`` codec (COMPLETION.md §3, §8.2).

Telegram limits inline-button ``callback_data`` to **64 bytes**, so wizard state is packed as
short colon-delimited opcodes carrying only numeric ids + 1-char selectors (never human-readable
payloads). :func:`encode` round-trips with :func:`decode`.

Opcodes:
  ``g:<fixture>``                     choose game (open the category step)
  ``c:<fixture>:<E|F|B|W|O>``         choose category
  ``s:<fixture>:<0-10>``              exact-score: home goals chosen (opens the away pad)
  ``e:<fixture>:<home>:<away>``       exact-score: finalize (home baked in, stateless)
  ``w:<fixture>:<H|D|A>``             winner selection
  ``t:<fixture>:<B|H|A|N>``           both-teams-to-score selection
  ``o:<fixture>:<O|U>``               over/under selection
  ``f:<fixture>:<H|A>``               first-team-to-score selection
  ``x:<bet_id>``                      delete a bet
  ``q``                               cancel/close the wizard
  ``bv:<g|s>``                        scoreboard view toggle (Geral / Semana)
  ``gb:<fixture>``                    per-game scoreboard for an ended game (§10)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never

from tigrinho.domain.bets import BetCategory, BttsSel, FirstTeamSel, OverUnderSel, WinnerSel

BoardScope = Literal["geral", "semana"]
_BOARD_SCOPE_TO_CODE: dict[BoardScope, str] = {"geral": "g", "semana": "s"}
_CODE_TO_BOARD_SCOPE: dict[str, BoardScope] = {"g": "geral", "s": "semana"}

MAX_CALLBACK_BYTES = 64

_CATEGORY_TO_CODE: dict[BetCategory, str] = {
    BetCategory.EXACT_SCORE: "E",
    BetCategory.FIRST_TEAM: "F",
    BetCategory.BTTS: "B",
    BetCategory.WINNER: "W",
    BetCategory.OVER_UNDER: "O",
}
_CODE_TO_CATEGORY = {code: category for category, code in _CATEGORY_TO_CODE.items()}

_FIRST_TEAM_TO_CODE: dict[FirstTeamSel, str] = {FirstTeamSel.HOME: "H", FirstTeamSel.AWAY: "A"}
_CODE_TO_FIRST_TEAM = {code: sel for sel, code in _FIRST_TEAM_TO_CODE.items()}

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
class HomeScore:
    fixture_id: int
    value: int


@dataclass(frozen=True, slots=True)
class ExactScore:
    fixture_id: int
    home: int
    away: int


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
class FirstTeamInput:
    fixture_id: int
    sel: FirstTeamSel


@dataclass(frozen=True, slots=True)
class DeleteBet:
    bet_id: int


@dataclass(frozen=True, slots=True)
class Cancel:
    pass


@dataclass(frozen=True, slots=True)
class BoardView:
    scope: BoardScope


@dataclass(frozen=True, slots=True)
class GameBoard:
    fixture_id: int


CallbackData = (
    ChooseGame
    | ChooseCategory
    | HomeScore
    | ExactScore
    | WinnerInput
    | BttsInput
    | OverUnderInput
    | FirstTeamInput
    | DeleteBet
    | Cancel
    | BoardView
    | GameBoard
)


def encode(data: CallbackData) -> str:
    """Encode wizard state to a ``callback_data`` string (≤ 64 bytes)."""
    match data:
        case ChooseGame(fixture_id):
            result = f"g:{fixture_id}"
        case ChooseCategory(fixture_id, category):
            result = f"c:{fixture_id}:{_CATEGORY_TO_CODE[category]}"
        case HomeScore(fixture_id, value):
            result = f"s:{fixture_id}:{value}"
        case ExactScore(fixture_id, home, away):
            result = f"e:{fixture_id}:{home}:{away}"
        case WinnerInput(fixture_id, sel):
            result = f"w:{fixture_id}:{_WINNER_TO_CODE[sel]}"
        case BttsInput(fixture_id, sel):
            result = f"t:{fixture_id}:{_BTTS_TO_CODE[sel]}"
        case OverUnderInput(fixture_id, sel):
            result = f"o:{fixture_id}:{_OVER_UNDER_TO_CODE[sel]}"
        case FirstTeamInput(fixture_id, sel):
            result = f"f:{fixture_id}:{_FIRST_TEAM_TO_CODE[sel]}"
        case DeleteBet(bet_id):
            result = f"x:{bet_id}"
        case Cancel():
            result = "q"
        case BoardView(scope):
            result = f"bv:{_BOARD_SCOPE_TO_CODE[scope]}"
        case GameBoard(fixture_id):
            result = f"gb:{fixture_id}"
        case _:  # pragma: no cover - exhaustiveness guard
            assert_never(data)
    if len(result.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data exceeds {MAX_CALLBACK_BYTES} bytes: {result!r}")
    return result


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
            return HomeScore(int(parts[1]), int(parts[2]))
        if op == "e":
            return ExactScore(int(parts[1]), int(parts[2]), int(parts[3]))
        if op == "w":
            return WinnerInput(int(parts[1]), _CODE_TO_WINNER[parts[2]])
        if op == "t":
            return BttsInput(int(parts[1]), _CODE_TO_BTTS[parts[2]])
        if op == "o":
            return OverUnderInput(int(parts[1]), _CODE_TO_OVER_UNDER[parts[2]])
        if op == "f":
            return FirstTeamInput(int(parts[1]), _CODE_TO_FIRST_TEAM[parts[2]])
        if op == "x":
            return DeleteBet(int(parts[1]))
        if op == "q":
            return Cancel()
        if op == "bv":
            return BoardView(_CODE_TO_BOARD_SCOPE[parts[1]])
        if op == "gb":
            return GameBoard(int(parts[1]))
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"invalid callback_data: {data!r}") from exc
    raise ValueError(f"unknown callback_data opcode: {data!r}")
