"""Tests for structlog setup (COMPLETION.md §14)."""

from __future__ import annotations

import json

import pytest

from tigrinho.logging import configure_logging, get_logger


def test_json_output_is_structured(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    log = get_logger("test")
    log.info("hello", fixture_id=42)
    out = capsys.readouterr().out.strip()
    payload = json.loads(out.splitlines()[-1])
    assert payload["event"] == "hello"
    assert payload["fixture_id"] == 42
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_console_output_contains_event(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG", "console")
    log = get_logger("test")
    log.warning("uh oh")
    out = capsys.readouterr().out
    assert "uh oh" in out


def test_level_filtering_suppresses_below_threshold(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("WARNING", "json")
    log = get_logger("test")
    log.info("should be filtered")
    log.error("should appear")
    out = capsys.readouterr().out
    assert "should be filtered" not in out
    assert "should appear" in out
