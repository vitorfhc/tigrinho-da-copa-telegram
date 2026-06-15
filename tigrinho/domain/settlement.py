"""Settlement: grade every bet for a finished fixture (COMPLETION.md §8.3).

PURE: no I/O, no clock, no DB. Deterministic and **idempotent** — re-running on the same inputs
yields identical grades. The caller (poll job / CLI) reads stored bets into ``PendingBet`` values,
calls :func:`settle_game`, then writes the resulting grades back to the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tigrinho.domain.bets import BetCategory, parse_payload
from tigrinho.domain.scoring import GradingContext, grade
from tigrinho.providers.base import MatchResult


@dataclass(frozen=True, slots=True)
class PendingBet:
    """A stored bet to grade (pure projection of the ORM ``Bet`` row)."""

    bet_id: int
    category: BetCategory
    payload_json: str


@dataclass(frozen=True, slots=True)
class GradedBet:
    """The grade computed for one bet (written back as is_correct/points_awarded)."""

    bet_id: int
    is_correct: bool
    points: int


def build_context(result: MatchResult, *, home_team_id: int, away_team_id: int) -> GradingContext:
    """Build a grading context from a finished MatchResult (requires a 90′ score)."""
    if result.home_goals_90 is None or result.away_goals_90 is None:
        raise ValueError(f"cannot settle fixture {result.fixture_id}: missing 90′ score")
    return GradingContext(
        home_goals_90=result.home_goals_90,
        away_goals_90=result.away_goals_90,
        stage=result.stage,
        advancing_team_id=result.advancing_team_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        goals=result.goals,
    )


def settle_game(
    bets: Sequence[PendingBet],
    result: MatchResult,
    *,
    home_team_id: int,
    away_team_id: int,
) -> list[GradedBet]:
    """Grade all bets on a fixture against its 90′ result (deterministic, idempotent)."""
    ctx = build_context(result, home_team_id=home_team_id, away_team_id=away_team_id)
    graded: list[GradedBet] = []
    for bet in bets:
        payload = parse_payload(bet.category, bet.payload_json)
        outcome = grade(payload, ctx)
        graded.append(
            GradedBet(bet_id=bet.bet_id, is_correct=outcome.is_correct, points=outcome.points)
        )
    return graded
