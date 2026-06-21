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
from tigrinho.enums import Stage
from tigrinho.providers.base import GoalEvent

# Single source of truth for the points table (§8.1) — trivially tunable. ``HALF_TIME_RESULT`` is
# priced like the old 3-way ``WINNER`` (modal ~45% → 2). The original four markets keep their
# prices so historical (legacy-regime) bets still award correctly (append-only).
POINTS: dict[BetCategory, int] = {
    BetCategory.EXACT_SCORE: 5,
    BetCategory.FIRST_TEAM: 2,
    BetCategory.BTTS: 2,
    BetCategory.WINNER: 2,
    BetCategory.OVER_UNDER: 1,
    BetCategory.HALF_TIME_RESULT: 2,
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
    # Half-time (regulation) score, for HALF_TIME_RESULT. Optional: not every result carries it
    # (walkovers, legacy CLI overrides), so it is validated lazily — a missing HT only voids a
    # HALF_TIME_RESULT bet, never the rest of the settlement.
    home_goals_ht: int | None = None
    away_goals_ht: int | None = None

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


def _first_team_outcome(ctx: GradingContext) -> FirstTeamSel | None:
    """Team that scored the first genuine (non-own-goal, ≤90′) goal; None on 0-0/own-goals-only."""
    scorer = first_genuine_scorer(ctx.goals)
    if scorer is None:
        return None
    if scorer.team_id == ctx.home_team_id:
        return FirstTeamSel.HOME
    if scorer.team_id == ctx.away_team_id:
        return FirstTeamSel.AWAY
    return None


def _half_time_outcome(ctx: GradingContext) -> HalfTimeSel | None:
    """Who led at the break from the regulation half-time score; None if HT is unavailable."""
    if ctx.home_goals_ht is None or ctx.away_goals_ht is None:
        return None
    if ctx.home_goals_ht > ctx.away_goals_ht:
        return HalfTimeSel.HOME
    if ctx.away_goals_ht > ctx.home_goals_ht:
        return HalfTimeSel.AWAY
    return HalfTimeSel.DRAW


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
    if isinstance(payload, FirstTeamPayload):
        first_team = _first_team_outcome(ctx)
        return first_team is not None and payload.sel is first_team
    if isinstance(payload, BttsPayload):
        return payload.sel is _btts_outcome(ctx)
    if isinstance(payload, WinnerPayload):
        winner = _winner_outcome(ctx)
        return winner is not None and payload.sel is winner
    if isinstance(payload, OverUnderPayload):
        if payload.sel is OverUnderSel.OVER:
            return ctx.total_goals_90 >= 3
        return ctx.total_goals_90 <= 2
    if isinstance(payload, HalfTimeResultPayload):
        half_time = _half_time_outcome(ctx)
        # Missing half-time score (walkover / no HT data) voids this market only.
        return half_time is not None and payload.sel is half_time
    assert_never(payload)  # pragma: no cover


def _exact_score_points(payload: ExactScorePayload, ctx: GradingContext) -> int:
    """Partial-credit scoring: +2 per correct team score, +1 if the outcome (win/draw) matches.

    Uses _winner_outcome so knockout advancing-team logic is identical to the WINNER bet.
    Maximum is 5 (exact score ≡ all three components correct).
    """
    pts = 0
    if payload.home == ctx.home_goals_90:
        pts += 2
    if payload.away == ctx.away_goals_90:
        pts += 2
    if payload.home > payload.away:
        predicted_outcome: WinnerSel = WinnerSel.HOME
    elif payload.away > payload.home:
        predicted_outcome = WinnerSel.AWAY
    else:
        predicted_outcome = WinnerSel.DRAW
    actual_outcome = _winner_outcome(ctx)
    if actual_outcome is not None and predicted_outcome is actual_outcome:
        pts += 1
    return pts


def grade(payload: Payload, ctx: GradingContext) -> BetGrade:
    """Grade a bet and award its category's points if correct (§8.1).

    EXACT_SCORE uses partial-credit scoring (see _exact_score_points); all other categories
    are all-or-nothing using the POINTS table.
    """
    if isinstance(payload, ExactScorePayload):
        pts = _exact_score_points(payload, ctx)
        return BetGrade(is_correct=pts == 5, points=pts)
    correct = is_correct(payload, ctx)
    return BetGrade(is_correct=correct, points=POINTS[payload.CATEGORY] if correct else 0)
