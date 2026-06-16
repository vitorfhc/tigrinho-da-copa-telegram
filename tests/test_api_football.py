"""Tests for ApiFootballProvider mapping + httpx client (COMPLETION.md §7.2, §16)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tigrinho.enums import GameStatus, Stage
from tigrinho.providers.api_football import (
    ApiFootballProvider,
    ProviderError,
    advancing_team_id,
    classify_stage,
    map_fixture,
    map_match_result,
    normalize_status,
    parse_goal_timeline,
    parse_goals,
)

# --- pure mapping ---------------------------------------------------------------------------


def test_normalize_status() -> None:
    assert normalize_status("NS") is GameStatus.SCHEDULED
    assert normalize_status("2H") is GameStatus.LIVE
    assert normalize_status("FT") is GameStatus.FINISHED
    assert normalize_status("AET") is GameStatus.FINISHED
    assert normalize_status("PEN") is GameStatus.FINISHED
    assert normalize_status("PST") is GameStatus.POSTPONED
    assert normalize_status("CANC") is GameStatus.CANCELLED
    assert normalize_status("ABD") is GameStatus.CANCELLED
    assert normalize_status("???") is GameStatus.SCHEDULED  # unknown -> safe default


def test_classify_stage() -> None:
    assert classify_stage("Group A - 1") is Stage.GROUP
    assert classify_stage("Round of 16") is Stage.KNOCKOUT
    assert classify_stage("Quarter-finals") is Stage.KNOCKOUT
    assert classify_stage("Semi-finals") is Stage.KNOCKOUT
    assert classify_stage("Final") is Stage.KNOCKOUT
    assert classify_stage("3rd Place Final") is Stage.KNOCKOUT
    assert classify_stage(None) is Stage.GROUP


def test_map_fixture_skips_placeholder() -> None:
    placeholder: dict[str, Any] = {
        "fixture": {"id": 5, "date": "2026-07-01T19:00:00+00:00", "status": {"short": "NS"}},
        "league": {"round": "Round of 16"},
        "teams": {"home": {"id": None, "name": "Winner Group A"}, "away": {"id": 20, "name": "X"}},
    }
    assert map_fixture(placeholder) is None


def test_map_fixture_parses_real_fixture() -> None:
    item: dict[str, Any] = {
        "fixture": {"id": 1001, "date": "2026-06-16T16:00:00-03:00", "status": {"short": "NS"}},
        "league": {"round": "Group A - 1"},
        "teams": {
            "home": {"id": 10, "name": "Brasil", "winner": None},
            "away": {"id": 20, "name": "Argentina", "winner": None},
        },
    }
    fixture = map_fixture(item)
    assert fixture is not None
    assert fixture.fixture_id == 1001
    assert fixture.stage is Stage.GROUP
    assert fixture.home_team_name == "Brasil"
    # -03:00 kickoff converted to UTC
    assert fixture.kickoff_utc == datetime(2026, 6, 16, 19, 0, tzinfo=UTC)


def test_parse_goals_filters_and_flags() -> None:
    events: list[dict[str, Any]] = [
        {
            "time": {"elapsed": 10, "extra": None},
            "team": {"id": 10},
            "player": {"id": 100, "name": "Neymar"},
            "type": "Goal",
            "detail": "Normal Goal",
        },
        {
            "time": {"elapsed": 20},
            "team": {"id": 20},
            "player": {"id": 200, "name": "Own Goaler"},
            "type": "Goal",
            "detail": "Own Goal",
        },
        {  # missed penalty -> excluded
            "time": {"elapsed": 30},
            "team": {"id": 10},
            "player": {"id": 100, "name": "Neymar"},
            "type": "Goal",
            "detail": "Missed Penalty",
        },
        {  # yellow card -> excluded
            "time": {"elapsed": 40},
            "team": {"id": 10},
            "player": {"id": 101, "name": "Casemiro"},
            "type": "Card",
            "detail": "Yellow Card",
        },
        {  # extra-time goal (>90) -> excluded
            "time": {"elapsed": 105},
            "team": {"id": 10},
            "player": {"id": 102, "name": "Late"},
            "type": "Goal",
            "detail": "Normal Goal",
        },
    ]
    goals = parse_goals(events)
    assert len(goals) == 2
    assert goals[0].player_name == "Neymar"
    assert goals[0].is_own_goal is False
    assert goals[1].is_own_goal is True
    assert goals[0].minute == 10


def test_advancing_team_id() -> None:
    assert advancing_team_id({"home": {"id": 10, "winner": True}, "away": {"id": 20}}) == 10
    assert advancing_team_id({"home": {"id": 10}, "away": {"id": 20, "winner": True}}) == 20
    assert advancing_team_id({"home": {"id": 10}, "away": {"id": 20}}) is None


def test_map_match_result_uses_fulltime_not_extratime() -> None:
    # Knockout that finished in extra time: 90′ was 1-1, ET made it 2-1, home advanced.
    item: dict[str, Any] = {
        "fixture": {"id": 2002, "status": {"short": "AET"}},
        "league": {"round": "Quarter-finals"},
        "teams": {"home": {"id": 10, "winner": True}, "away": {"id": 20, "winner": False}},
        "goals": {"home": 2, "away": 1},
        "score": {
            "fulltime": {"home": 1, "away": 1},
            "extratime": {"home": 2, "away": 1},
            "penalty": {"home": None, "away": None},
        },
    }
    result = map_match_result(item, [])
    assert result.home_goals_90 == 1  # 90′ regulation, not the 2 from extra time
    assert result.away_goals_90 == 1
    assert result.status is GameStatus.FINISHED
    assert result.stage is Stage.KNOCKOUT
    assert result.advancing_team_id == 10


# --- httpx client (MockTransport) -----------------------------------------------------------


def _provider(handler: object, *, now: datetime | None = None) -> ApiFootballProvider:
    client = httpx.AsyncClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    fixed = now or datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
    return ApiFootballProvider(
        base_url="http://test.local",
        api_key="k",
        league_id=1,
        season=2026,
        client=client,
        clock=lambda: fixed,
    )


async def test_get_fixtures_filters_window_and_placeholders() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {
            "errors": [],
            "response": [
                {  # in window
                    "fixture": {
                        "id": 1,
                        "date": "2026-06-16T19:00:00+00:00",
                        "status": {"short": "NS"},
                    },
                    "league": {"round": "Group A - 1"},
                    "teams": {"home": {"id": 10, "name": "A"}, "away": {"id": 20, "name": "B"}},
                },
                {  # before now -> filtered out
                    "fixture": {
                        "id": 2,
                        "date": "2026-06-15T05:00:00+00:00",
                        "status": {"short": "NS"},
                    },
                    "league": {"round": "Group A - 1"},
                    "teams": {"home": {"id": 11, "name": "C"}, "away": {"id": 21, "name": "D"}},
                },
                {  # beyond cutoff -> filtered out
                    "fixture": {
                        "id": 3,
                        "date": "2026-06-20T19:00:00+00:00",
                        "status": {"short": "NS"},
                    },
                    "league": {"round": "Group A - 1"},
                    "teams": {"home": {"id": 12, "name": "E"}, "away": {"id": 22, "name": "F"}},
                },
                {  # placeholder -> skipped
                    "fixture": {
                        "id": 4,
                        "date": "2026-06-16T20:00:00+00:00",
                        "status": {"short": "NS"},
                    },
                    "league": {"round": "Round of 16"},
                    "teams": {"home": {"id": None, "name": "Winner A"}, "away": {"id": 23}},
                },
            ],
        }
        return httpx.Response(200, json=body)

    provider = _provider(handler)
    fixtures = await provider.get_fixtures(48)
    assert [f.fixture_id for f in fixtures] == [1]
    await provider.aclose()


async def test_get_match_result_calls_fixtures_then_events() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/fixtures":
            return httpx.Response(
                200,
                json={
                    "errors": [],
                    "response": [
                        {
                            "fixture": {"id": 1001, "status": {"short": "FT"}},
                            "league": {"round": "Group A - 1"},
                            "teams": {"home": {"id": 10}, "away": {"id": 20}},
                            "score": {"fulltime": {"home": 2, "away": 1}},
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "time": {"elapsed": 12},
                        "team": {"id": 10},
                        "player": {"id": 100, "name": "Neymar"},
                        "type": "Goal",
                        "detail": "Normal Goal",
                    }
                ],
            },
        )

    provider = _provider(handler)
    result = await provider.get_match_result(1001)
    assert result.home_goals_90 == 2
    assert result.goals[0].player_name == "Neymar"
    assert paths == ["/fixtures", "/fixtures/events"]
    await provider.aclose()


async def test_get_live_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "fixture": {"id": 1001, "status": {"short": "2H"}},
                        "league": {"round": "Group A - 1"},
                        "teams": {"home": {"id": 10}, "away": {"id": 20}},
                        "score": {"fulltime": {"home": 1, "away": 0}},
                    }
                ],
            },
        )

    provider = _provider(handler)
    live = await provider.get_live_results()
    assert len(live) == 1
    assert live[0].status is GameStatus.LIVE
    await provider.aclose()


async def test_error_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": {"token": "invalid"}, "response": []})

    provider = _provider(handler)
    with pytest.raises(ProviderError):
        await provider.get_fixtures(48)
    await provider.aclose()


async def test_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    provider = _provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.get_match_result(10)
    await provider.aclose()


async def test_get_live_results_carries_live_score() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "fixture": {"id": 1001, "status": {"short": "2H"}},
                        "league": {"round": "Group A - 1"},
                        "teams": {"home": {"id": 10}, "away": {"id": 20}},
                        "goals": {"home": 2, "away": 1},
                        "score": {"fulltime": {"home": None, "away": None}},
                    }
                ],
            },
        )

    provider = _provider(handler)
    live = await provider.get_live_results()
    assert live[0].live_home_goals == 2
    assert live[0].live_away_goals == 1
    assert live[0].home_goals_90 is None  # 90′ score still null mid-match
    await provider.aclose()


def test_parse_goal_timeline_keeps_extra_time_and_flags() -> None:
    events: list[dict[str, Any]] = [
        {
            "time": {"elapsed": 10, "extra": None},
            "team": {"id": 10},
            "player": {"id": 100, "name": "Neymar"},
            "type": "Goal",
            "detail": "Normal Goal",
        },
        {  # extra-time goal (>90) -> KEPT (unlike parse_goals)
            "time": {"elapsed": 105, "extra": 2},
            "team": {"id": 20},
            "player": {"id": 200, "name": "Late"},
            "type": "Goal",
            "detail": "Penalty",
        },
        {  # missed penalty -> excluded
            "time": {"elapsed": 30},
            "team": {"id": 10},
            "player": {"id": 100, "name": "Neymar"},
            "type": "Goal",
            "detail": "Missed Penalty",
        },
        {  # penalty shootout (null elapsed) -> excluded
            "time": {"elapsed": None},
            "team": {"id": 10},
            "player": {"id": 101, "name": "Shooter"},
            "type": "Goal",
            "detail": "Penalty",
        },
    ]
    goals = parse_goal_timeline(events)
    assert len(goals) == 2
    assert goals[0].minute == 10
    assert goals[1].minute == 105
    assert goals[1].extra == 2
    assert goals[1].is_penalty is True


async def test_get_goal_events_calls_events_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "time": {"elapsed": 23, "extra": None},
                        "team": {"id": 10},
                        "player": {"id": 100, "name": "Vini"},
                        "type": "Goal",
                        "detail": "Normal Goal",
                    }
                ],
            },
        )

    provider = _provider(handler)
    goals = await provider.get_goal_events(1001)
    assert [g.player_name for g in goals] == ["Vini"]
    assert paths == ["/fixtures/events"]
    await provider.aclose()
