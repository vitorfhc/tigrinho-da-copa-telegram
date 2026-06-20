"""Validated schema for the Gemini AI palpite response (COMPLETION.md §20).

The model is instructed to return a single JSON object; the Python backend extracts and
**validates** it with these pydantic models before anything is stored. Selections reuse the
domain bet enums (:mod:`tigrinho.domain.bets`), so an AI palpite renders through the same
``describe_bet`` path as a human bet and can never carry an out-of-range value.

PURE: no I/O, no clock, no DB.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tigrinho.domain.bets import (
    BetCategory,
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstTeamPayload,
    FirstTeamSel,
    HalfTimeResultPayload,
    HalfTimeSel,
    OverUnderPayload,
    OverUnderSel,
    Payload,
    WinnerPayload,
    WinnerSel,
)

# Gemini grounding inserts inline citation tags like ``[1]`` / ``[1.1.7]`` / ``[2, 3]`` into the
# grounded text. They are noise in a chat message, so we strip them from free-text fields.
_CITATION_RE = re.compile(r"\s*\[\d[\d.,\s]*\]")


def strip_citation_tags(text: str) -> str:
    """Remove grounding citation tags (``[1]``, ``[1.1.7]``, …) and tidy the spacing."""
    cleaned = _CITATION_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


class _AiModel(BaseModel):
    # Tolerate extra keys the model may volunteer; validate the ones we care about strictly.
    model_config = ConfigDict(extra="ignore")


class ExactScorePrediction(_AiModel):
    home: int = Field(ge=0, le=99)
    away: int = Field(ge=0, le=99)


class GamePalpite(_AiModel):
    """The AI's prediction for one fixture across every bet category, plus its reasoning."""

    fixture_id: int
    analysis: str
    exact_score: ExactScorePrediction
    first_team: FirstTeamSel
    btts: BttsSel
    winner: WinnerSel
    over_under: OverUnderSel
    # Who leads at the break (the new orthogonal market). Optional so palpites cached *before* this
    # field existed still validate on load; new generations always include it (the prompt asks).
    half_time_result: HalfTimeSel | None = None
    # A real, web-grounded curiosity about this head-to-head; empty when none was found (the model
    # is told never to invent one).
    curiosity: str = ""

    @field_validator("analysis", "curiosity")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        return strip_citation_tags(value)

    def payloads(self, categories: Sequence[BetCategory] | None = None) -> list[Payload]:
        """The predicted bet payloads, filtered to ``categories`` (a game's offered set) when given.

        With no filter, returns every prediction the model supplied (legacy callers). Predictions
        for categories the game does not offer — or a half-time result absent on an old cached row —
        are simply skipped.
        """
        available: dict[BetCategory, Payload] = {
            BetCategory.EXACT_SCORE: ExactScorePayload(
                home=self.exact_score.home, away=self.exact_score.away
            ),
            BetCategory.FIRST_TEAM: FirstTeamPayload(sel=self.first_team),
            BetCategory.BTTS: BttsPayload(sel=self.btts),
            BetCategory.WINNER: WinnerPayload(sel=self.winner),
            BetCategory.OVER_UNDER: OverUnderPayload(sel=self.over_under),
        }
        if self.half_time_result is not None:
            available[BetCategory.HALF_TIME_RESULT] = HalfTimeResultPayload(
                sel=self.half_time_result
            )
        order = (
            tuple(categories)
            if categories is not None
            else (
                BetCategory.EXACT_SCORE,
                BetCategory.HALF_TIME_RESULT,
                BetCategory.FIRST_TEAM,
                BetCategory.BTTS,
                BetCategory.WINNER,
                BetCategory.OVER_UNDER,
            )
        )
        return [available[c] for c in order if c in available]


class PalpiteBatch(_AiModel):
    """The top-level response: one :class:`GamePalpite` per analyzed fixture."""

    palpites: list[GamePalpite]


def extract_json(text: str) -> str:
    """Return the JSON object embedded in a model response (handles code fences / prose).

    The model is asked for raw JSON, but grounded responses sometimes wrap it in a ```json
    fence or surround it with prose. We slice from the first ``{`` to the last ``}``.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model response")
    return text[start : end + 1]


def parse_batch(text: str) -> PalpiteBatch:
    """Extract + validate a model response into a :class:`PalpiteBatch` (raises on bad data)."""
    return PalpiteBatch.model_validate_json(extract_json(text))
