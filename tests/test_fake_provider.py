"""Tests for provider value objects + FakeProvider (COMPLETION.md §7.1, §16)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from tigrinho.enums import GameStatus, Stage
from tigrinho.providers.base import (
    Fixture,
    FootballProvider,
    GoalEvent,
    MatchResult,
    SquadPlayer,
)
from tigrinho.providers.fake import FakeProvider


def _fixture(fixture_id: int = 1001) -> Fixture:
    return Fixture(
        fixture_id=fixture_id,
        stage=Stage.GROUP,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=datetime(2026, 6, 16, 19, 0, tzinfo=UTC),
        status=GameStatus.SCHEDULED,
    )


def _result(fixture_id: int = 1001) -> MatchResult:
    return MatchResult(
        fixture_id=fixture_id,
        stage=Stage.GROUP,
        status=GameStatus.FINISHED,
        home_goals_90=2,
        away_goals_90=1,
        goals=(
            GoalEvent(
                minute=10,
                team_id=10,
                player_id=100,
                player_name="Neymar",
                is_own_goal=False,
                is_penalty=False,
            ),
        ),
        advancing_team_id=None,
    )


def test_value_objects_are_frozen() -> None:
    fixture = _fixture()
    with pytest.raises(dataclasses.FrozenInstanceError):
        fixture.fixture_id = 99  # type: ignore[misc]


def test_fake_provider_satisfies_protocol() -> None:
    provider: FootballProvider = FakeProvider()
    assert isinstance(provider, FootballProvider)


async def test_fake_provider_returns_scripted_data() -> None:
    squad = [SquadPlayer(player_id=100, team_id=10, name="Neymar", position="FW")]
    provider = FakeProvider(
        fixtures=[_fixture(1001), _fixture(1002)],
        results=[_result(1001)],
        squads={10: squad},
    )

    fixtures = await provider.get_fixtures(48)
    assert [f.fixture_id for f in fixtures] == [1001, 1002]

    live = await provider.get_live_results()
    assert [r.fixture_id for r in live] == [1001]

    result = await provider.get_match_result(1001)
    assert result.home_goals_90 == 2
    assert result.goals[0].player_name == "Neymar"

    assert await provider.get_squad(10) == squad
    assert await provider.get_squad(999) == []


async def test_fake_provider_logs_calls() -> None:
    provider = FakeProvider(results=[_result(1001)])
    await provider.get_fixtures(48)
    await provider.get_live_results()
    await provider.get_match_result(1001)
    await provider.get_squad(10)
    assert provider.call_log == [
        "get_fixtures",
        "get_live_results",
        "get_match_result:1001",
        "get_squad:10",
    ]
