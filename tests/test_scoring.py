"""Exhaustive grading tests (COMPLETION.md §8.1, §16) — scoring.py to ~100% branch coverage."""

from __future__ import annotations

import pytest

from tigrinho.domain.bets import (
    BetCategory,
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstScorerPayload,
    OverUnderPayload,
    OverUnderSel,
    Payload,
    WinnerPayload,
    WinnerSel,
)
from tigrinho.domain.scoring import (
    POINTS,
    GradingContext,
    first_genuine_scorer,
    grade,
    is_correct,
)
from tigrinho.enums import Stage
from tigrinho.providers.base import GoalEvent


def _goal(minute: int, team_id: int, player_id: int, *, own: bool = False) -> GoalEvent:
    return GoalEvent(
        minute=minute,
        team_id=team_id,
        player_id=player_id,
        player_name=f"p{player_id}",
        is_own_goal=own,
        is_penalty=False,
    )


def _ctx(
    home: int,
    away: int,
    *,
    stage: Stage = Stage.GROUP,
    advancing: int | None = None,
    home_id: int = 10,
    away_id: int = 20,
    goals: tuple[GoalEvent, ...] = (),
) -> GradingContext:
    return GradingContext(
        home_goals_90=home,
        away_goals_90=away,
        stage=stage,
        advancing_team_id=advancing,
        home_team_id=home_id,
        away_team_id=away_id,
        goals=goals,
    )


# --- points table ---------------------------------------------------------------------------


def test_points_table() -> None:
    assert POINTS == {
        BetCategory.EXACT_SCORE: 5,
        BetCategory.FIRST_SCORER: 4,
        BetCategory.BTTS: 2,
        BetCategory.WINNER: 2,
        BetCategory.OVER_UNDER: 1,
    }


# --- exact score ----------------------------------------------------------------------------


def test_exact_score() -> None:
    assert is_correct(ExactScorePayload(home=2, away=1), _ctx(2, 1)) is True
    assert is_correct(ExactScorePayload(home=2, away=1), _ctx(1, 1)) is False
    assert is_correct(ExactScorePayload(home=2, away=1), _ctx(2, 0)) is False


# --- winner (group + knockout) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("home", "away", "sel", "expected"),
    [
        (2, 1, WinnerSel.HOME, True),
        (2, 1, WinnerSel.DRAW, False),
        (2, 1, WinnerSel.AWAY, False),
        (1, 1, WinnerSel.DRAW, True),
        (1, 1, WinnerSel.HOME, False),
        (0, 2, WinnerSel.AWAY, True),
        (0, 2, WinnerSel.DRAW, False),
    ],
)
def test_winner_group(home: int, away: int, sel: WinnerSel, expected: bool) -> None:
    assert is_correct(WinnerPayload(sel=sel), _ctx(home, away)) is expected


def test_winner_knockout_advancing_team() -> None:
    ko = {"stage": Stage.KNOCKOUT, "home_id": 10, "away_id": 20}
    # 90′ draw, away (20) advanced on penalties.
    ctx = _ctx(1, 1, advancing=20, **ko)  # type: ignore[arg-type]
    assert is_correct(WinnerPayload(sel=WinnerSel.AWAY), ctx) is True
    assert is_correct(WinnerPayload(sel=WinnerSel.HOME), ctx) is False
    assert is_correct(WinnerPayload(sel=WinnerSel.DRAW), ctx) is False  # never draw in knockout
    # home advanced
    ctx_home = _ctx(1, 1, advancing=10, **ko)  # type: ignore[arg-type]
    assert is_correct(WinnerPayload(sel=WinnerSel.HOME), ctx_home) is True


def test_winner_knockout_fallback_to_90_when_no_advancing() -> None:
    # decisive 90′ result, no advancing-team info -> use the 90′ winner
    assert is_correct(WinnerPayload(sel=WinnerSel.HOME), _ctx(2, 1, stage=Stage.KNOCKOUT)) is True
    assert is_correct(WinnerPayload(sel=WinnerSel.AWAY), _ctx(0, 1, stage=Stage.KNOCKOUT)) is True
    # 90′ draw + no advancing info -> nobody is correct
    draw = _ctx(0, 0, stage=Stage.KNOCKOUT)
    assert is_correct(WinnerPayload(sel=WinnerSel.HOME), draw) is False
    assert is_correct(WinnerPayload(sel=WinnerSel.AWAY), draw) is False
    assert is_correct(WinnerPayload(sel=WinnerSel.DRAW), draw) is False


# --- BTTS -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("home", "away", "sel", "expected"),
    [
        (1, 1, BttsSel.BOTH, True),
        (2, 0, BttsSel.ONLY_HOME, True),
        (0, 3, BttsSel.ONLY_AWAY, True),
        (0, 0, BttsSel.NEITHER, True),
        (1, 1, BttsSel.NEITHER, False),
        (2, 0, BttsSel.BOTH, False),
        (0, 1, BttsSel.ONLY_HOME, False),
    ],
)
def test_btts(home: int, away: int, sel: BttsSel, expected: bool) -> None:
    assert is_correct(BttsPayload(sel=sel), _ctx(home, away)) is expected


# --- over / under boundary at exactly 2 and 3 -----------------------------------------------


@pytest.mark.parametrize(
    ("home", "away", "sel", "expected"),
    [
        (1, 1, OverUnderSel.UNDER, True),  # total 2 -> under
        (1, 1, OverUnderSel.OVER, False),
        (2, 1, OverUnderSel.OVER, True),  # total 3 -> over
        (2, 1, OverUnderSel.UNDER, False),
        (0, 0, OverUnderSel.UNDER, True),
        (3, 3, OverUnderSel.OVER, True),
    ],
)
def test_over_under_boundary(home: int, away: int, sel: OverUnderSel, expected: bool) -> None:
    assert is_correct(OverUnderPayload(sel=sel), _ctx(home, away)) is expected


# --- first scorer ---------------------------------------------------------------------------


def test_first_genuine_scorer_skips_own_goals_and_extra_time() -> None:
    goals = (
        _goal(10, 20, 200, own=True),  # own goal -> skipped
        _goal(95, 10, 999),  # extra time (>90) -> skipped
        _goal(50, 10, 100),  # first genuine 90′ scorer
    )
    scorer = first_genuine_scorer(goals)
    assert scorer is not None
    assert scorer.player_id == 100


def test_first_scorer_grading() -> None:
    goals = (_goal(10, 10, 100), _goal(60, 20, 200))
    assert is_correct(FirstScorerPayload(player_id=100), _ctx(1, 1, goals=goals)) is True
    assert is_correct(FirstScorerPayload(player_id=200), _ctx(1, 1, goals=goals)) is False


def test_first_scorer_loses_on_0_0() -> None:
    assert is_correct(FirstScorerPayload(player_id=100), _ctx(0, 0, goals=())) is False


def test_first_scorer_loses_when_only_own_goals() -> None:
    goals = (_goal(15, 20, 200, own=True),)
    assert is_correct(FirstScorerPayload(player_id=200), _ctx(1, 0, goals=goals)) is False


# --- grade() points awarding ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "ctx", "expected_correct", "expected_points"),
    [
        (ExactScorePayload(home=2, away=1), _ctx(2, 1), True, 5),
        (ExactScorePayload(home=2, away=1), _ctx(0, 0), False, 0),
        (FirstScorerPayload(player_id=100), _ctx(1, 0, goals=(_goal(5, 10, 100),)), True, 4),
        (BttsPayload(sel=BttsSel.BOTH), _ctx(1, 1), True, 2),
        (WinnerPayload(sel=WinnerSel.HOME), _ctx(3, 0), True, 2),
        (OverUnderPayload(sel=OverUnderSel.OVER), _ctx(2, 1), True, 1),
        (OverUnderPayload(sel=OverUnderSel.OVER), _ctx(1, 1), False, 0),
    ],
)
def test_grade_awards_points(
    payload: Payload, ctx: GradingContext, expected_correct: bool, expected_points: int
) -> None:
    result = grade(payload, ctx)
    assert result.is_correct is expected_correct
    assert result.points == expected_points
