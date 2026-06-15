"""Football data provider interface + value objects (COMPLETION.md §7.1).

This layer is platform-agnostic and carries over unchanged from the Discord build. Providers
return **frozen value objects** (never raw JSON). Datetimes are **timezone-aware UTC**; the data
layer converts to its naive-UTC storage convention when persisting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from tigrinho.enums import GameStatus, Stage


@dataclass(frozen=True, slots=True)
class Fixture:
    """An upcoming/known World Cup fixture with both real teams decided."""

    fixture_id: int
    stage: Stage
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    kickoff_utc: datetime  # timezone-aware UTC
    status: GameStatus


@dataclass(frozen=True, slots=True)
class GoalEvent:
    """A single goal event within a fixture's timeline (§7.2)."""

    minute: int
    team_id: int
    player_id: int | None
    player_name: str | None
    is_own_goal: bool
    is_penalty: bool


@dataclass(frozen=True, slots=True)
class MatchResult:
    """A fixture's result + ordered goal timeline (§7.1).

    ``home_goals_90`` / ``away_goals_90`` are the **90′ regulation** score (``score.fulltime``),
    excluding extra time. ``advancing_team_id`` is the knockout winner (from ET/penalties), used
    only for the knockout winner rule. ``goals`` is ordered earliest-first.
    """

    fixture_id: int
    stage: Stage
    status: GameStatus
    home_goals_90: int | None
    away_goals_90: int | None
    goals: tuple[GoalEvent, ...]
    advancing_team_id: int | None


@dataclass(frozen=True, slots=True)
class SquadPlayer:
    """A squad member for first-scorer selection (cached; §7.1)."""

    player_id: int
    team_id: int
    name: str
    position: str | None


@runtime_checkable
class FootballProvider(Protocol):
    """Provider-agnostic interface returning value objects (§7.1)."""

    async def get_fixtures(self, window_hours: int) -> list[Fixture]:
        """Upcoming WC fixtures within ``window_hours`` (placeholders/TBD excluded)."""
        ...

    async def get_live_results(self) -> list[MatchResult]:
        """One call returning every currently-live WC fixture's result."""
        ...

    async def get_match_result(self, fixture_id: int) -> MatchResult:
        """Final result + goal timeline for one fixture."""
        ...

    async def get_squad(self, team_id: int) -> list[SquadPlayer]:
        """Cached squad for a team (first-scorer selection)."""
        ...


# Convenience aliases for scripting a FakeProvider.
FixtureSeq = Sequence[Fixture]
ResultSeq = Sequence[MatchResult]
SquadMap = Mapping[int, Sequence[SquadPlayer]]
