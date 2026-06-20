"""Football data provider interface + value objects (COMPLETION.md §7.1).

This layer is platform-agnostic and carries over unchanged from the Discord build. Providers
return **frozen value objects** (never raw JSON). Datetimes are **timezone-aware UTC**; the data
layer converts to its naive-UTC storage convention when persisting.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    team_id: int  # the team the goal counts FOR — for own goals this is the *benefiting* side
    # (API-Football credits own goals to the opponent; ``player`` is the own-goaler), so callers
    # must NOT flip it. See ``domain/live.goal_progression``.
    player_id: int | None
    player_name: str | None
    is_own_goal: bool
    is_penalty: bool
    extra: int | None = None  # added stoppage minutes (e.g. 90+`extra`); for live display


@dataclass(frozen=True, slots=True)
class VarCancellation:
    """A goal disallowed/cancelled by VAR within a fixture's timeline (§9.4).

    ``detail`` is the raw provider string (e.g. ``"Goal cancelled"`` or
    ``"Goal Disallowed - offside"``) so the display layer can derive a reason without the provider
    layer hard-coding pt-BR text.
    """

    minute: int
    team_id: int
    player_name: str | None
    detail: str
    extra: int | None = None  # added stoppage minutes (e.g. 90+`extra`); for live display


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
    live_home_goals: int | None = None  # current running score (incl. ET); from item.goals
    live_away_goals: int | None = None


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

    async def get_goal_events(self, fixture_id: int) -> tuple[GoalEvent, ...]:
        """Full goal timeline (incl. extra time) for live notifications (§9.4)."""
        ...

    async def get_goal_cancellations(self, fixture_id: int) -> tuple[VarCancellation, ...]:
        """VAR goal-cancellation events for one fixture (live notifications, §9.4)."""
        ...


# Convenience aliases for scripting a FakeProvider.
FixtureSeq = Sequence[Fixture]
ResultSeq = Sequence[MatchResult]
