"""Tests for the real Gemini generator (the google-genai SDK is mocked — no network)."""

from __future__ import annotations

from typing import Any

import pytest

from tigrinho.ai.base import PalpiteGenerator
from tigrinho.ai.gemini import GeminiGameScorer, GeminiPalpiteGenerator


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any], *, text: str | None
) -> None:
    class FakeModels:
        async def generate_content(
            self, *, model: str, contents: Any, config: Any
        ) -> _FakeResponse:
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return _FakeResponse(text)

    class FakeAio:
        def __init__(self) -> None:
            self.models = FakeModels()

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            captured["api_key"] = api_key
            self.aio = FakeAio()

    monkeypatch.setattr("tigrinho.ai.gemini.genai.Client", FakeClient)


async def test_generator_satisfies_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, {}, text="{}")
    gen = GeminiPalpiteGenerator(api_key="k", model="gemini-3.1-pro-preview")
    assert isinstance(gen, PalpiteGenerator)


async def test_generate_passes_grounding_and_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_client(monkeypatch, captured, text='{"palpites": []}')
    gen = GeminiPalpiteGenerator(api_key="secret", model="gemini-3.1-pro-preview")

    out = await gen.generate(system_instruction="SYS", user_content="USER")

    assert out == '{"palpites": []}'
    assert captured["api_key"] == "secret"
    assert captured["model"] == "gemini-3.1-pro-preview"
    assert captured["contents"] == "USER"
    config = captured["config"]
    assert config.system_instruction == "SYS"
    # The SDK normalizes the string to a ThinkingLevel enum (value "HIGH").
    assert config.thinking_config.thinking_level.value == "HIGH"
    # Google Search grounding tool is attached.
    assert config.tools[0].google_search is not None


async def test_generate_raises_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, {}, text=None)
    gen = GeminiPalpiteGenerator(api_key="k", model="gemini-3.1-pro-preview")
    with pytest.raises(ValueError, match="empty"):
        await gen.generate(system_instruction="s", user_content="u")


async def test_score_games_passes_grounding_and_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _install_fake_client(monkeypatch, captured, text='{"name": "X", "scores": []}')
    scorer = GeminiGameScorer(api_key="secret", model="gemini-3.1-pro-preview")

    out = await scorer.score_games(system_instruction="SYS", user_content="USER")

    assert out == '{"name": "X", "scores": []}'
    assert captured["model"] == "gemini-3.1-pro-preview"
    assert captured["contents"] == "USER"
    config = captured["config"]
    assert config.system_instruction == "SYS"
    assert config.thinking_config.thinking_level.value == "HIGH"
    assert config.tools[0].google_search is not None


async def test_score_games_empty_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_client(monkeypatch, captured, text=None)
    scorer = GeminiGameScorer(api_key="secret", model="gemini-3.1-pro-preview")
    with pytest.raises(ValueError):
        await scorer.score_games(system_instruction="SYS", user_content="USER")
