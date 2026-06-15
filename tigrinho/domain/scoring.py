"""Centralized points table + per-category grading (COMPLETION.md §8.1).

PURE: no I/O, no clock, no DB; deterministic. All score-based grading uses the **90′ result**.
This module + ``settlement.py`` carry the project's correctness-critical logic and are held to
~100% line+branch coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

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
from tigrinho.enums import Stage
from tigrinho.providers.base import GoalEvent

# Single source of truth for the points table (§8.1) — trivially tunable.
POINTS: dict[BetCategory, int] = {
    BetCategory.EXACT_SCORE: 5,
    BetCategory.FIRST_SCORER: 4,
    BetCategory.BTTS: 2,
    BetCategory.WINNER: 2,
    BetCategory.OVER_UNDER: 1,
}


@dataclass(frozen=True, slots=True)
class GradingContext:
    """Everything the grading rules need about a finished fixture (the 90′ result + context)."""

    home_goals_90: int
    away_goals_90: int
    stage: Stage
    advancing_team_id: int | None
    home_team_id: int
    away_team_id: int
    goals: tuple[GoalEvent, ...]

    @property
    def total_goals_90(self) -> int:
        return self.home_goals_90 + self.away_goals_90


@dataclass(frozen=True, slots=True)
class BetGrade:
    """The graded outcome of a single bet."""

    is_correct: bool
    points: int


def first_genuine_scorer(goals: tuple[GoalEvent, ...]) -> GoalEvent | None:
    """First non-own-goal scorer within 90′ (own goals skipped); None on 0-0 / own-goals-only."""
    for goal in goals:
        if not goal.is_own_goal and goal.minute <= 90:
            return goal
    return None


def _winner_outcome(ctx: GradingContext) -> WinnerSel | None:
    """The official 1X2 outcome: 90′ result for group; advancing team for knockout (no draw)."""
    if ctx.stage is Stage.KNOCKOUT:
        if ctx.advancing_team_id == ctx.home_team_id:
            return WinnerSel.HOME
        if ctx.advancing_team_id == ctx.away_team_id:
            return WinnerSel.AWAY
        # No advancing-team info: fall back to the 90′ result if it was decisive.
        if ctx.home_goals_90 > ctx.away_goals_90:
            return WinnerSel.HOME
        if ctx.away_goals_90 > ctx.home_goals_90:
            return WinnerSel.AWAY
        return None  # true draw with unknown shootout winner -> no side is correct
    if ctx.home_goals_90 > ctx.away_goals_90:
        return WinnerSel.HOME
    if ctx.away_goals_90 > ctx.home_goals_90:
        return WinnerSel.AWAY
    return WinnerSel.DRAW


def _btts_outcome(ctx: GradingContext) -> BttsSel:
    home_scored = ctx.home_goals_90 > 0
    away_scored = ctx.away_goals_90 > 0
    if home_scored and away_scored:
        return BttsSel.BOTH
    if home_scored:
        return BttsSel.ONLY_HOME
    if away_scored:
        return BttsSel.ONLY_AWAY
    return BttsSel.NEITHER


def is_correct(payload: Payload, ctx: GradingContext) -> bool:
    """Grade one bet against the 90′ result (PURE)."""
    if isinstance(payload, ExactScorePayload):
        return payload.home == ctx.home_goals_90 and payload.away == ctx.away_goals_90
    if isinstance(payload, FirstScorerPayload):
        scorer = first_genuine_scorer(ctx.goals)
        return scorer is not None and scorer.player_id == payload.player_id
    if isinstance(payload, BttsPayload):
        return payload.sel is _btts_outcome(ctx)
    if isinstance(payload, WinnerPayload):
        outcome = _winner_outcome(ctx)
        return outcome is not None and payload.sel is outcome
    if isinstance(payload, OverUnderPayload):
        if payload.sel is OverUnderSel.OVER:
            return ctx.total_goals_90 >= 3
        return ctx.total_goals_90 <= 2
    assert_never(payload)  # pragma: no cover


def grade(payload: Payload, ctx: GradingContext) -> BetGrade:
    """Grade a bet and award its category's points if correct (§8.1)."""
    correct = is_correct(payload, ctx)
    return BetGrade(is_correct=correct, points=POINTS[payload.CATEGORY] if correct else 0)
