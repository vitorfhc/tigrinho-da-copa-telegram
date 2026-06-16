"""Pure live-goal helpers for in-match group notifications (COMPLETION.md §9.4).

Walks a goal timeline and reconstructs the running score, applying the **own-goal flip** (an own
goal counts for the *opposing* side). No I/O, clock, or DB — deterministic and testable.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from tigrinho.providers.base import GoalEvent


class Side(enum.StrEnum):
    """Which team's tally a goal increases."""

    HOME = "HOME"
    AWAY = "AWAY"


@dataclass(frozen=True, slots=True)
class GoalProgress:
    """A goal plus the running score *after* it and the side it counted for."""

    goal: GoalEvent
    home_score: int
    away_score: int
    scoring_side: Side


def goal_progression(
    home_team_id: int, away_team_id: int, goals: Sequence[GoalEvent]
) -> list[GoalProgress]:
    """Reconstruct the running score for ``goals`` (chronological), own-goal flip applied."""
    home = 0
    away = 0
    out: list[GoalProgress] = []
    for goal in goals:
        scored_for_home = goal.team_id == home_team_id
        if goal.is_own_goal:
            scored_for_home = not scored_for_home
        if scored_for_home:
            home += 1
            side = Side.HOME
        else:
            away += 1
            side = Side.AWAY
        out.append(GoalProgress(goal=goal, home_score=home, away_score=away, scoring_side=side))
    return out
