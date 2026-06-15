"""Scripted football provider for tests and local dev (``provider_mode: fake``; §7.1).

Holds pre-built value objects and replays them. Records a ``call_log`` so tests can assert how
many provider calls a job made (e.g. the active-window polling decision in §9.2).
"""

from __future__ import annotations

from tigrinho.providers.base import (
    Fixture,
    FixtureSeq,
    MatchResult,
    ResultSeq,
    SquadMap,
    SquadPlayer,
)


class FakeProvider:
    """In-memory :class:`~tigrinho.providers.base.FootballProvider` implementation."""

    def __init__(
        self,
        *,
        fixtures: FixtureSeq | None = None,
        results: ResultSeq | None = None,
        squads: SquadMap | None = None,
    ) -> None:
        self._fixtures: list[Fixture] = list(fixtures or [])
        self._results: dict[int, MatchResult] = {r.fixture_id: r for r in (results or [])}
        self._squads: dict[int, list[SquadPlayer]] = {
            team_id: list(players) for team_id, players in (squads or {}).items()
        }
        self.call_log: list[str] = []

    async def get_fixtures(self, window_hours: int) -> list[Fixture]:
        self.call_log.append("get_fixtures")
        return list(self._fixtures)

    async def get_live_results(self) -> list[MatchResult]:
        self.call_log.append("get_live_results")
        return list(self._results.values())

    async def get_match_result(self, fixture_id: int) -> MatchResult:
        self.call_log.append(f"get_match_result:{fixture_id}")
        return self._results[fixture_id]

    async def get_squad(self, team_id: int) -> list[SquadPlayer]:
        self.call_log.append(f"get_squad:{team_id}")
        return list(self._squads.get(team_id, []))
