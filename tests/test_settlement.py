"""Settlement tests: full-game grading + idempotency (COMPLETION.md §8.3, §16)."""

from __future__ import annotations

import pytest

from tigrinho.domain.bets import (
    BetCategory,
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstTeamPayload,
    FirstTeamSel,
    OverUnderPayload,
    OverUnderSel,
    WinnerPayload,
    WinnerSel,
    serialize_payload,
)
from tigrinho.domain.settlement import (
    GradedBet,
    PendingBet,
    build_context,
    settle_game,
)
from tigrinho.enums import GameStatus, Stage
from tigrinho.providers.base import GoalEvent, MatchResult


def _result(
    home: int | None,
    away: int | None,
    *,
    stage: Stage = Stage.GROUP,
    advancing: int | None = None,
    goals: tuple[GoalEvent, ...] = (),
) -> MatchResult:
    return MatchResult(
        fixture_id=1001,
        stage=stage,
        status=GameStatus.FINISHED,
        home_goals_90=home,
        away_goals_90=away,
        goals=goals,
        advancing_team_id=advancing,
    )


def test_settle_full_game() -> None:
    bets = [
        PendingBet(
            1, BetCategory.EXACT_SCORE, serialize_payload(ExactScorePayload(home=2, away=1))
        ),
        PendingBet(2, BetCategory.WINNER, serialize_payload(WinnerPayload(sel=WinnerSel.HOME))),
        PendingBet(
            3, BetCategory.OVER_UNDER, serialize_payload(OverUnderPayload(sel=OverUnderSel.UNDER))
        ),
        PendingBet(4, BetCategory.BTTS, serialize_payload(BttsPayload(sel=BttsSel.BOTH))),
    ]
    graded = settle_game(bets, _result(2, 1), home_team_id=10, away_team_id=20)
    by_id = {g.bet_id: (g.is_correct, g.points) for g in graded}
    assert by_id == {
        1: (True, 5),  # exact 2-1
        2: (True, 2),  # winner HOME
        3: (False, 0),  # under, but total is 3
        4: (True, 2),  # both scored
    }


def test_settle_is_idempotent() -> None:
    bets = [
        PendingBet(
            1, BetCategory.EXACT_SCORE, serialize_payload(ExactScorePayload(home=2, away=1))
        ),
        PendingBet(2, BetCategory.WINNER, serialize_payload(WinnerPayload(sel=WinnerSel.HOME))),
    ]
    result = _result(2, 1)
    first = settle_game(bets, result, home_team_id=10, away_team_id=20)
    second = settle_game(bets, result, home_team_id=10, away_team_id=20)
    assert first == second
    assert first == [GradedBet(1, True, 5), GradedBet(2, True, 2)]


def test_settle_knockout_winner_uses_advancing_team() -> None:
    bets = [
        PendingBet(1, BetCategory.WINNER, serialize_payload(WinnerPayload(sel=WinnerSel.AWAY))),
    ]
    result = _result(1, 1, stage=Stage.KNOCKOUT, advancing=20)
    graded = settle_game(bets, result, home_team_id=10, away_team_id=20)
    assert graded[0].is_correct is True


def test_settle_first_team() -> None:
    goals = (
        GoalEvent(
            minute=10,
            team_id=10,  # home team scores first
            player_id=100,
            player_name="Neymar",
            is_own_goal=False,
            is_penalty=False,
        ),
    )
    bets = [
        PendingBet(
            1, BetCategory.FIRST_TEAM, serialize_payload(FirstTeamPayload(sel=FirstTeamSel.HOME))
        ),
        PendingBet(
            2, BetCategory.FIRST_TEAM, serialize_payload(FirstTeamPayload(sel=FirstTeamSel.AWAY))
        ),
    ]
    graded = settle_game(bets, _result(1, 0, goals=goals), home_team_id=10, away_team_id=20)
    assert graded[0] == GradedBet(1, True, 2)
    assert graded[1] == GradedBet(2, False, 0)


def test_settle_empty_bets() -> None:
    assert settle_game([], _result(0, 0), home_team_id=10, away_team_id=20) == []


def test_build_context_requires_home_score() -> None:
    with pytest.raises(ValueError, match="90"):
        build_context(_result(None, 1), home_team_id=10, away_team_id=20)


def test_build_context_requires_away_score() -> None:
    with pytest.raises(ValueError, match="90"):
        build_context(_result(1, None), home_team_id=10, away_team_id=20)
