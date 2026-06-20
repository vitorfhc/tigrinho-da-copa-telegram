"""Pure live-goal helpers for in-match group notifications (COMPLETION.md §9.4).

Walks a goal timeline and reconstructs the running score. ``GoalEvent.team_id`` is the side the
goal counts *for* — API-Football already attributes an own goal to the benefiting team (the
own-goaler is the event's ``player``), so **no flip** is applied. No I/O, clock, or DB —
deterministic and testable.
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
    """Reconstruct the running score for ``goals`` (chronological).

    ``GoalEvent.team_id`` is the side the goal counts *for* (own goals included — the provider
    credits them to the benefiting team), so it is used as-is with no flip.
    """
    home = 0
    away = 0
    out: list[GoalProgress] = []
    for goal in goals:
        scored_for_home = goal.team_id == home_team_id
        if scored_for_home:
            home += 1
            side = Side.HOME
        else:
            away += 1
            side = Side.AWAY
        out.append(GoalProgress(goal=goal, home_score=home, away_score=away, scoring_side=side))
    return out
