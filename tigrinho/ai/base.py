"""The AI palpite generator interface (COMPLETION.md §20).

A tiny Protocol so the service layer depends on an abstraction, not on ``google-genai``: the
real :class:`~tigrinho.ai.gemini.GeminiPalpiteGenerator` does the grounded call, while tests
inject a fake. Returning raw text (not a parsed object) keeps validation in one place
(:func:`tigrinho.ai.schemas.parse_batch`).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PalpiteGenerator(Protocol):
    """Produces the raw model text (expected to contain the palpite JSON)."""

    async def generate(self, *, system_instruction: str, user_content: str) -> str: ...


@runtime_checkable
class GameScorer(Protocol):
    """Grades candidate games on binary interest criteria; returns raw model text (JSON; §24)."""

    async def score_games(self, *, system_instruction: str, user_content: str) -> str: ...
