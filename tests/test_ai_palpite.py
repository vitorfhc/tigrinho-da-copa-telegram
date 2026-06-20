"""Tests for the pure AI palpite layer: schemas, JSON extraction, prompt building."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from tigrinho.ai.prompt import GameInfo, build_palpite_prompt
from tigrinho.ai.schemas import (
    PalpiteBatch,
    extract_json,
    parse_batch,
    strip_citation_tags,
)
from tigrinho.domain.bets import (
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstTeamPayload,
    FirstTeamSel,
    HalfTimeResultPayload,
    HalfTimeSel,
    OverUnderPayload,
    OverUnderSel,
    WinnerPayload,
    WinnerSel,
    offerable_for,
)
from tigrinho.enums import CategorySet, Stage

_VALID_JSON = """
{
  "palpites": [
    {
      "fixture_id": 101,
      "analysis": "Brasil vem em alta, mas a Argentina tem boa defesa.",
      "exact_score": {"home": 2, "away": 1},
      "first_team": "HOME",
      "btts": "BOTH",
      "winner": "HOME",
      "over_under": "OVER",
      "curiosity": "As duas seleções decidiram a última final continental."
    }
  ]
}
"""


def test_parse_valid_batch() -> None:
    batch = parse_batch(_VALID_JSON)
    assert isinstance(batch, PalpiteBatch)
    assert len(batch.palpites) == 1
    game = batch.palpites[0]
    assert game.fixture_id == 101
    assert game.exact_score.home == 2
    assert game.first_team is FirstTeamSel.HOME
    assert game.curiosity == "As duas seleções decidiram a última final continental."


def test_game_palpite_to_typed_payloads() -> None:
    game = parse_batch(_VALID_JSON).palpites[0]
    payloads = game.payloads()
    # No half_time_result in this (old-style) JSON -> the legacy five, HT skipped.
    assert payloads == [
        ExactScorePayload(home=2, away=1),
        FirstTeamPayload(sel=FirstTeamSel.HOME),
        BttsPayload(sel=BttsSel.BOTH),
        WinnerPayload(sel=WinnerSel.HOME),
        OverUnderPayload(sel=OverUnderSel.OVER),
    ]


def test_game_palpite_v2_filter_includes_half_time() -> None:
    json_with_ht = _VALID_JSON.replace(
        '"over_under": "OVER",',
        '"over_under": "OVER",\n      "half_time_result": "DRAW",',
    )
    game = parse_batch(json_with_ht).palpites[0]
    assert game.half_time_result is HalfTimeSel.DRAW
    # The V2 regime renders only EXACT_SCORE + HALF_TIME_RESULT, in that order.
    assert game.payloads(offerable_for(CategorySet.V2)) == [
        ExactScorePayload(home=2, away=1),
        HalfTimeResultPayload(sel=HalfTimeSel.DRAW),
    ]


def test_extract_json_strips_markdown_fences() -> None:
    fenced = "```json\n" + _VALID_JSON.strip() + "\n```"
    batch = parse_batch(fenced)
    assert batch.palpites[0].fixture_id == 101


def test_extract_json_ignores_surrounding_prose() -> None:
    noisy = "Aqui está a análise:\n" + _VALID_JSON.strip() + "\nEspero que ajude!"
    assert extract_json(noisy).startswith("{")
    assert extract_json(noisy).endswith("}")


def test_extract_json_raises_when_no_object() -> None:
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("sorry, I cannot help with that")


def test_curiosity_optional_defaults_empty() -> None:
    no_cur = _VALID_JSON.replace(
        ',\n      "curiosity": "As duas seleções decidiram a última final continental."', ""
    )
    game = parse_batch(no_cur).palpites[0]
    assert game.curiosity == ""


def test_strips_citation_tags_from_analysis_and_curiosity() -> None:
    raw = """
    {"palpites": [{
      "fixture_id": 7,
      "analysis": "A França chega forte [1.1.7], com Mbappé em alta [2].",
      "exact_score": {"home": 2, "away": 0},
      "first_team": "HOME", "btts": "ONLY_HOME", "winner": "HOME", "over_under": "UNDER",
      "curiosity": "Já se enfrentaram em 2018 [3.4]."
    }]}
    """
    game = parse_batch(raw).palpites[0]
    assert "[" not in game.analysis and "]" not in game.analysis
    assert game.analysis == "A França chega forte, com Mbappé em alta."
    assert game.curiosity == "Já se enfrentaram em 2018."


def test_strip_citation_tags_helper() -> None:
    assert strip_citation_tags("texto [1] aqui [2.3.4].") == "texto aqui."
    assert strip_citation_tags("sem citações.") == "sem citações."
    assert strip_citation_tags("") == ""


def test_invalid_winner_selection_rejected() -> None:
    bad = _VALID_JSON.replace('"winner": "HOME"', '"winner": "MAYBE"')
    with pytest.raises(ValidationError):
        parse_batch(bad)


def test_exact_score_out_of_range_rejected() -> None:
    bad = _VALID_JSON.replace('"home": 2', '"home": 999')
    with pytest.raises(ValidationError):
        parse_batch(bad)


def test_extra_keys_are_ignored() -> None:
    extra = _VALID_JSON.replace(
        '"over_under": "OVER",', '"over_under": "OVER",\n      "bogus_extra": "whatever",'
    )
    game = parse_batch(extra).palpites[0]
    assert game.fixture_id == 101


def _game(fixture_id: int, stage: Stage = Stage.GROUP) -> GameInfo:
    return GameInfo(
        fixture_id=fixture_id,
        home_team="Brasil",
        away_team="Argentina",
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        stage=stage,
    )


def test_prompt_lists_each_game() -> None:
    _system, user = build_palpite_prompt([_game(101), _game(202, Stage.KNOCKOUT)])
    assert "101" in user
    assert "202" in user
    assert "Brasil" in user and "Argentina" in user
    assert "16/06" in user


def test_prompt_system_instruction_covers_rules_and_grounding() -> None:
    system, _user = build_palpite_prompt([_game(101)])
    lowered = system.lower()
    assert "json" in lowered  # must demand structured JSON output
    assert "90" in system  # graded on the 90-minute result
    # knockout has no draw; over/under threshold; grounding via web search
    assert "empate" in lowered  # explains the no-draw knockout rule in pt-BR
    assert "pesquis" in lowered or "web" in lowered  # instruct to search the web (grounding)
    # curiosity must be grounded-only (no hallucination): leave empty if none found
    assert "curiosidade" in lowered
    assert "invente" in lowered or "vazi" in lowered  # "não invente" / leave empty rule


def test_prompt_marks_knockout_stage() -> None:
    _system, user = build_palpite_prompt([_game(202, Stage.KNOCKOUT)])
    assert "mata-mata" in user.lower() or "knockout" in user.lower()
