"""Gemini-backed AI palpite generator (COMPLETION.md §20).

Grounding (per §2), verified June 2026 against **google-genai 2.8.0**:
- Gemini 3 developer guide — https://ai.google.dev/gemini-api/docs/gemini-3
  Client: ``genai.Client(api_key=...)``; async client at ``client.aio``; thinking via
  ``types.ThinkingConfig(thinking_level="high")`` (default for 3.x is already "high"). Model id
  ``gemini-3.1-pro-preview`` (the user-requested "Gemini 3.1 Pro"; ``gemini-3-pro-preview`` now
  redirects to it).
- Grounding with Google Search — https://ai.google.dev/gemini-api/docs/google-search
  Enable with ``tools=[types.Tool(google_search=types.GoogleSearch())]``.

Design decisions (recorded in COMPLETION.md §20):
- We use the **google-genai SDK directly**, not the ADK agent framework: ADK's built-in
  ``google_search`` tool is documented as Gemini-2 only, and a single non-interactive grounded
  call is exactly what the SDK exposes.
- We get JSON by **instructing it in the prompt + validating in the backend**
  (:func:`tigrinho.ai.schemas.parse_batch`) rather than ``response_schema``, which historically
  could not be combined with the Google Search tool. The validation guarantees a clean JSON.
- Network work runs on the SDK's native async client (``client.aio``) so the bot event loop is
  never blocked (the project's async-network / sync-DB split).
"""

from __future__ import annotations

from google import genai
from google.genai import types

from tigrinho.logging import get_logger

_log = get_logger("tigrinho.ai.gemini")


class GeminiPalpiteGenerator:
    """Calls Gemini 3.1 Pro with Google Search grounding and returns the raw response text."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate(self, *, system_instruction: str, user_content: str) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=config,
        )
        text = response.text
        if not text:
            raise ValueError("Gemini returned an empty response")
        _log.info("palpite_generated", model=self._model, chars=len(text))
        return text


class GeminiGameScorer:
    """Daily-bolãozinho game-interest scorer — a separate Gemini flow from /palpite (§24).

    Same grounded-call conventions as :class:`GeminiPalpiteGenerator` (Google Search + high
    thinking), but a distinct method/schema so the two flows never share state.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def score_games(self, *, system_instruction: str, user_content: str) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=config,
        )
        text = response.text
        if not text:
            raise ValueError("Gemini returned an empty game-scoring response")
        _log.info("game_scoring_generated", model=self._model, chars=len(text))
        return text
