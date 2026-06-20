"""Bet categories, typed payload models, and (de)serialization (COMPLETION.md §8.1).

PURE: no I/O, no clock, no DB. Payloads are validated pydantic models serialized to the
``bets.payload_json`` column. ``parse_payload`` rebuilds the right model from a category +
JSON string; ``serialize_payload`` produces the JSON string to store.
"""

from __future__ import annotations

import enum
from typing import ClassVar, assert_never

from pydantic import BaseModel, ConfigDict, Field

from tigrinho.enums import CategorySet


class BetCategory(enum.StrEnum):
    """The bet categories (§8.1).

    **Append-only.** ``HALF_TIME_RESULT`` is the current orthogonal market (with ``EXACT_SCORE``);
    ``FIRST_TEAM``/``BTTS``/``WINNER``/``OVER_UNDER`` are the original markets, kept so bets placed
    before the new set still grade and render — they are merely no longer *offered* (see
    :data:`OFFERABLE`). Never remove a member: stored bets reference it by value.
    """

    EXACT_SCORE = "EXACT_SCORE"
    FIRST_TEAM = "FIRST_TEAM"
    BTTS = "BTTS"
    WINNER = "WINNER"
    OVER_UNDER = "OVER_UNDER"
    HALF_TIME_RESULT = "HALF_TIME_RESULT"


class WinnerSel(enum.StrEnum):
    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"


class FirstTeamSel(enum.StrEnum):
    """Which team scores the first genuine goal within 90′ (no draw — a goal has a team)."""

    HOME = "HOME"
    AWAY = "AWAY"


class BttsSel(enum.StrEnum):
    BOTH = "BOTH"
    ONLY_HOME = "ONLY_HOME"
    ONLY_AWAY = "ONLY_AWAY"
    NEITHER = "NEITHER"


class OverUnderSel(enum.StrEnum):
    OVER = "OVER"
    UNDER = "UNDER"


class HalfTimeSel(enum.StrEnum):
    """Who leads at the half-time break — DRAW is always valid (a half can be level)."""

    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExactScorePayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.EXACT_SCORE
    home: int = Field(ge=0, le=99)
    away: int = Field(ge=0, le=99)


class FirstTeamPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.FIRST_TEAM
    sel: FirstTeamSel


class BttsPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.BTTS
    sel: BttsSel


class WinnerPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.WINNER
    sel: WinnerSel


class OverUnderPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.OVER_UNDER
    sel: OverUnderSel


class HalfTimeResultPayload(_Payload):
    CATEGORY: ClassVar[BetCategory] = BetCategory.HALF_TIME_RESULT
    sel: HalfTimeSel


Payload = (
    ExactScorePayload
    | FirstTeamPayload
    | BttsPayload
    | WinnerPayload
    | OverUnderPayload
    | HalfTimeResultPayload
)


# Which categories are *offered* per game regime (§8.1 rollout). Distinct from gradeable: every
# BetCategory still grades (append-only), but only these are presented in the wizard / palpite /
# bettor-count denominator. ``EXACT_SCORE`` is shared; the rest are the regime's orthogonal markets.
OFFERABLE: dict[CategorySet, tuple[BetCategory, ...]] = {
    CategorySet.LEGACY: (
        BetCategory.EXACT_SCORE,
        BetCategory.FIRST_TEAM,
        BetCategory.BTTS,
        BetCategory.WINNER,
        BetCategory.OVER_UNDER,
    ),
    CategorySet.V2: (
        BetCategory.EXACT_SCORE,
        BetCategory.HALF_TIME_RESULT,
    ),
}


def offerable_for(category_set: CategorySet) -> tuple[BetCategory, ...]:
    """The bet categories a game in ``category_set`` offers (wizard / palpite / denominator)."""
    return OFFERABLE[category_set]


def parse_payload(category: BetCategory, payload_json: str) -> Payload:
    """Rebuild the typed payload model for ``category`` from its stored JSON (validates)."""
    match category:
        case BetCategory.EXACT_SCORE:
            return ExactScorePayload.model_validate_json(payload_json)
        case BetCategory.FIRST_TEAM:
            return FirstTeamPayload.model_validate_json(payload_json)
        case BetCategory.BTTS:
            return BttsPayload.model_validate_json(payload_json)
        case BetCategory.WINNER:
            return WinnerPayload.model_validate_json(payload_json)
        case BetCategory.OVER_UNDER:
            return OverUnderPayload.model_validate_json(payload_json)
        case BetCategory.HALF_TIME_RESULT:
            return HalfTimeResultPayload.model_validate_json(payload_json)
    assert_never(category)  # pragma: no cover


def serialize_payload(payload: Payload) -> str:
    """Serialize a payload model to the JSON string stored in ``bets.payload_json``."""
    return payload.model_dump_json()
