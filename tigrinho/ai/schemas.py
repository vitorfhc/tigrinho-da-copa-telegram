"""Validated schema for the Gemini AI palpite response (COMPLETION.md §20).

The model is instructed to return a single JSON object; the Python backend extracts and
**validates** it with these pydantic models before anything is stored. Selections reuse the
domain bet enums (:mod:`tigrinho.domain.bets`), so an AI palpite renders through the same
``describe_bet`` path as a human bet and can never carry an out-of-range value.

PURE: no I/O, no clock, no DB.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from tigrinho.domain.bets import (
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
)


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
    confidence: int | None = Field(default=None, ge=0, le=100)

    def payloads(self) -> list[Payload]:
        """Convert to the domain's typed bet payloads (in :data:`CATEGORY_ORDER` order)."""
        return [
            ExactScorePayload(home=self.exact_score.home, away=self.exact_score.away),
            FirstTeamPayload(sel=self.first_team),
            BttsPayload(sel=self.btts),
            WinnerPayload(sel=self.winner),
            OverUnderPayload(sel=self.over_under),
        ]


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
