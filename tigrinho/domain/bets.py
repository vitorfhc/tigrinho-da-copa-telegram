"""Bet categories, typed payload models, and (de)serialization (COMPLETION.md §8.1).

PURE: no I/O, no clock, no DB. Payloads are validated pydantic models serialized to the
``bets.payload_json`` column. ``parse_payload`` rebuilds the right model from a category +
JSON string; ``serialize_payload`` produces the JSON string to store.
"""

from __future__ import annotations

import enum
from typing import ClassVar, assert_never

from pydantic import BaseModel, ConfigDict, Field


class BetCategory(enum.StrEnum):
    """The five bet categories (§8.1)."""

    EXACT_SCORE = "EXACT_SCORE"
    FIRST_SCORER = "FIRST_SCORER"
    BTTS = "BTTS"
    WINNER = "WINNER"
    OVER_UNDER = "OVER_UNDER"


class WinnerSel(enum.StrEnum):
    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"


class BttsSel(enum.StrEnum):
    BOTH = "BOTH"
    ONLY_HOME = "ONLY_HOME"
    ONLY_AWAY = "ONLY_AWAY"
    NEITHER = "NEITHER"


class OverUnderSel(enum.StrEnum):
    OVER = "OVER"
    UNDER = "UNDER"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExactScorePayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.EXACT_SCORE
    home: int = Field(ge=0, le=99)
    away: int = Field(ge=0, le=99)


class FirstScorerPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.FIRST_SCORER
    player_id: int = Field(gt=0)


class BttsPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.BTTS
    sel: BttsSel


class WinnerPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.WINNER
    sel: WinnerSel


class OverUnderPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.OVER_UNDER
    sel: OverUnderSel


Payload = ExactScorePayload | FirstScorerPayload | BttsPayload | WinnerPayload | OverUnderPayload


def parse_payload(category: BetCategory, payload_json: str) -> Payload:
    """Rebuild the typed payload model for ``category`` from its stored JSON (validates)."""
    match category:
        case BetCategory.EXACT_SCORE:
            return ExactScorePayload.model_validate_json(payload_json)
        case BetCategory.FIRST_SCORER:
            return FirstScorerPayload.model_validate_json(payload_json)
        case BetCategory.BTTS:
            return BttsPayload.model_validate_json(payload_json)
        case BetCategory.WINNER:
            return WinnerPayload.model_validate_json(payload_json)
        case BetCategory.OVER_UNDER:
            return OverUnderPayload.model_validate_json(payload_json)
    assert_never(category)  # pragma: no cover


def serialize_payload(payload: Payload) -> str:
    """Serialize a payload model to the JSON string stored in ``bets.payload_json``."""
    return payload.model_dump_json()
