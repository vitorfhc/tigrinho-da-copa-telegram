"""Tests for the pure live-goal progression helper (COMPLETION.md §9.4)."""

from __future__ import annotations

from tigrinho.domain.live import Side, goal_progression
from tigrinho.providers.base import GoalEvent


def _g(
    minute: int, team_id: int, *, own: bool = False, pen: bool = False, extra: int | None = None
) -> GoalEvent:
    return GoalEvent(
        minute=minute,
        team_id=team_id,
        player_id=None,
        player_name=None,
        is_own_goal=own,
        is_penalty=pen,
        extra=extra,
    )


def test_goal_progression_running_score() -> None:
    prog = goal_progression(10, 20, [_g(10, 10), _g(25, 20), _g(70, 10)])
    assert [(p.home_score, p.away_score, p.scoring_side) for p in prog] == [
        (1, 0, Side.HOME),
        (1, 1, Side.AWAY),
        (2, 1, Side.HOME),
    ]


def test_goal_progression_own_goal_credited_to_event_team() -> None:
    # API-Football attributes an own-goal event to the *benefiting* team (the side the goal counts
    # for), with the own-goaler as ``player`` — so ``team_id`` is already the scoring side and must
    # NOT be flipped. An away (20) player nets into their own goal -> the event's team is home (10),
    # which scores: 1 x 0, credited to HOME.
    prog = goal_progression(10, 20, [_g(30, 10, own=True)])
    assert (prog[0].home_score, prog[0].away_score) == (1, 0)
    assert prog[0].scoring_side is Side.HOME


def test_goal_progression_extra_time_preserved() -> None:
    prog = goal_progression(10, 20, [_g(105, 10, extra=2)])
    assert prog[0].goal.minute == 105
    assert prog[0].goal.extra == 2
    assert prog[0].home_score == 1


def test_goal_progression_empty() -> None:
    assert goal_progression(10, 20, []) == []
