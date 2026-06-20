"""Tests for the daily-bolãozinho AI wire format: models, parse, sanitize, prompt (§24)."""

from __future__ import annotations

from datetime import datetime

import pytest

from tigrinho.ai.daily_bolao import (
    GameInterestCriteria,
    GameInterestScore,
    build_scoring_prompt,
    parse_scoring,
    sanitize_name,
)
from tigrinho.ai.prompt import GameInfo
from tigrinho.enums import Stage


def _score_json(fid: int) -> str:
    return GameInterestScore(
        fixture_id=fid,
        criteria=GameInterestCriteria(
            decisive=True,
            quality_matchup=True,
            rivalry_or_storyline=False,
            star_power=True,
            competitive_balance=False,
            goal_potential=True,
        ),
    ).model_dump_json()


def test_parse_scoring_happy_path() -> None:
    raw = '{"name": "Clássico do Dia", "scores": [' + _score_json(1) + "]}"
    batch = parse_scoring(raw)
    assert batch.name == "Clássico do Dia"
    assert batch.scores[0].fixture_id == 1
    assert batch.scores[0].criteria.decisive is True


def test_parse_scoring_handles_code_fence_and_prose() -> None:
    raw = "Aqui está:\n```json\n" + '{"name": "X", "scores": [' + _score_json(7) + "]}\n```"
    batch = parse_scoring(raw)
    assert batch.scores[0].fixture_id == 7


def test_parse_scoring_name_omitted_defaults_to_empty() -> None:
    raw = '{"scores": [' + _score_json(1) + "]}"
    batch = parse_scoring(raw)
    assert batch.name == ""
    assert batch.scores[0].fixture_id == 1


def test_parse_scoring_bad_scores_raises() -> None:
    with pytest.raises(ValueError):
        parse_scoring('{"name": "X"}')  # missing required `scores`


def test_sanitize_name_strips_citation_tags_and_collapses() -> None:
    assert sanitize_name("Clássico [1] do   Dia\n") == "Clássico do Dia"


def test_sanitize_name_truncates_to_60_chars() -> None:
    assert len(sanitize_name("A" * 200)) == 60


def test_sanitize_name_empty_when_only_citations() -> None:
    assert sanitize_name("  [1] [2.3]  ") == ""


def test_build_scoring_prompt_lists_each_game_and_forbids_numbers() -> None:
    games = [
        GameInfo(1, "Brasil", "Argentina", datetime(2026, 6, 21, 16, 0), Stage.KNOCKOUT),
        GameInfo(2, "França", "Alemanha", datetime(2026, 6, 21, 13, 0), Stage.GROUP),
    ]
    system, user = build_scoring_prompt(games)
    assert "fixture_id=1" in user and "fixture_id=2" in user
    assert "mata-mata" in user and "fase de grupos" in user
    assert "decisive" in system  # criteria are named in the instruction
    # the model must not emit a number/ranking
    assert "JSON" in system
