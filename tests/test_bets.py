"""Tests for bet payload models + (de)serialization (COMPLETION.md §8.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tigrinho.domain.bets import (
    BetCategory,
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstTeamPayload,
    FirstTeamSel,
    OverUnderPayload,
    OverUnderSel,
    Payload,
    WinnerPayload,
    WinnerSel,
    parse_payload,
    serialize_payload,
)


@pytest.mark.parametrize(
    ("category", "payload"),
    [
        (BetCategory.EXACT_SCORE, ExactScorePayload(home=3, away=2)),
        (BetCategory.FIRST_TEAM, FirstTeamPayload(sel=FirstTeamSel.HOME)),
        (BetCategory.BTTS, BttsPayload(sel=BttsSel.ONLY_AWAY)),
        (BetCategory.WINNER, WinnerPayload(sel=WinnerSel.DRAW)),
        (BetCategory.OVER_UNDER, OverUnderPayload(sel=OverUnderSel.OVER)),
    ],
)
def test_serialize_parse_round_trip(category: BetCategory, payload: Payload) -> None:
    restored = parse_payload(category, serialize_payload(payload))
    assert restored == payload
    assert restored.CATEGORY is category


def test_exact_score_rejects_negative_and_too_large() -> None:
    with pytest.raises(ValidationError):
        ExactScorePayload(home=-1, away=0)
    with pytest.raises(ValidationError):
        ExactScorePayload(home=0, away=100)


def test_first_team_rejects_unknown_selection() -> None:
    with pytest.raises(ValidationError):
        FirstTeamPayload.model_validate({"sel": "DRAW"})


def test_winner_rejects_unknown_selection() -> None:
    with pytest.raises(ValidationError):
        WinnerPayload.model_validate({"sel": "MAYBE"})


def test_payload_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        ExactScorePayload.model_validate({"home": 1, "away": 1, "extra": 9})


def test_parse_payload_validates_json() -> None:
    with pytest.raises(ValidationError):
        parse_payload(BetCategory.EXACT_SCORE, '{"home": -5, "away": 1}')
