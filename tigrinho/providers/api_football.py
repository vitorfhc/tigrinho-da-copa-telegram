"""API-Football v3 provider (COMPLETION.md §7.2).

Grounding (per §2), verified June 2026 (the v3 response shapes; the docs site blocks automated
fetch so structures were confirmed via API-Football help articles + the published v3 schema):
- Base host ``https://v3.football.api-sports.io``; API key sent in the ``x-apisports-key`` header.
- ``GET /fixtures?league=&season=&from=&to=&timezone=UTC`` → ``response[]`` with ``fixture.id``,
  ``fixture.date`` (ISO8601 w/ offset), ``fixture.status.short``, ``league.round``,
  ``teams.{home,away}.{id,name,winner}``, ``goals.{home,away}``,
  ``score.{halftime,fulltime,extratime,penalty}.{home,away}``.
- ``GET /fixtures?live=all`` → currently-live fixtures (same shape).
- ``GET /fixtures?id=`` → a single fixture.
- ``GET /fixtures/events?fixture=`` → ``response[]`` with ``time.{elapsed,extra}``, ``team.id``,
  ``player.{id,name}``, ``type`` (``Goal``/``Card``/``subst``/``Var``), ``detail``
  (``Normal Goal``/``Own Goal``/``Penalty``/``Missed Penalty``).

Mapping rules (§7.2): the **90′ score** is ``score.fulltime`` (NOT goals/extratime); the advancing
team is the side with ``winner == true``; the goal timeline keeps goals with ``elapsed <= 90``
(stoppage included, ET excluded), flagging own goals / penalties and excluding ``Missed Penalty``.
The pure domain decides the first scorer from these flags.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tigrinho.enums import GameStatus, Stage
from tigrinho.providers.base import Fixture, GoalEvent, MatchResult, VarCancellation

# fixture.status.short → normalized GameStatus (§7.2; INT/SUSP→LIVE and AWD/WO→FINISHED added as
# the simplest §2-consistent extension for codes the spec did not enumerate).
_STATUS_MAP: dict[str, GameStatus] = {
    "TBD": GameStatus.SCHEDULED,
    "NS": GameStatus.SCHEDULED,
    "1H": GameStatus.LIVE,
    "HT": GameStatus.LIVE,
    "2H": GameStatus.LIVE,
    "ET": GameStatus.LIVE,
    "BT": GameStatus.LIVE,
    "P": GameStatus.LIVE,
    "LIVE": GameStatus.LIVE,
    "INT": GameStatus.LIVE,
    "SUSP": GameStatus.LIVE,
    "FT": GameStatus.FINISHED,
    "AET": GameStatus.FINISHED,
    "PEN": GameStatus.FINISHED,
    "AWD": GameStatus.FINISHED,
    "WO": GameStatus.FINISHED,
    "PST": GameStatus.POSTPONED,
    "CANC": GameStatus.CANCELLED,
    "ABD": GameStatus.CANCELLED,
}

_KNOCKOUT_KEYWORDS = (
    "round of",
    "8th finals",
    "quarter",
    "semi",
    "final",
    "play-off",
    "playoff",
    "3rd place",
    "third place",
)


class ProviderError(RuntimeError):
    """Raised when API-Football returns an error payload or an unexpected response."""


def _utcnow_aware() -> datetime:
    return datetime.now(tz=UTC)


def normalize_status(short: str) -> GameStatus:
    return _STATUS_MAP.get(short, GameStatus.SCHEDULED)


def classify_stage(round_name: str | None) -> Stage:
    if round_name and any(kw in round_name.lower() for kw in _KNOCKOUT_KEYWORDS):
        return Stage.KNOCKOUT
    return Stage.GROUP


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def map_fixture(item: dict[str, Any]) -> Fixture | None:
    """Map one ``/fixtures`` item to a Fixture, or None for placeholders (undecided teams)."""
    fixture = item.get("fixture") or {}
    teams = item.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_id = _opt_int(home.get("id"))
    away_id = _opt_int(away.get("id"))
    if home_id is None or away_id is None:
        return None  # placeholder fixture ("Winner Group A" / TBD)
    raw_date = fixture.get("date")
    if not raw_date:
        return None
    kickoff = datetime.fromisoformat(raw_date).astimezone(UTC)
    status = normalize_status((fixture.get("status") or {}).get("short", ""))
    stage = classify_stage((item.get("league") or {}).get("round"))
    return Fixture(
        fixture_id=int(fixture["id"]),
        stage=stage,
        home_team_id=home_id,
        home_team_name=str(home.get("name", "")),
        away_team_id=away_id,
        away_team_name=str(away.get("name", "")),
        kickoff_utc=kickoff,
        status=status,
    )


def parse_goals(events: list[dict[str, Any]]) -> tuple[GoalEvent, ...]:
    """Goal timeline within 90′ (stoppage included), in API chronological order (§7.2)."""
    goals: list[GoalEvent] = []
    for event in events:
        if event.get("type") != "Goal":
            continue
        detail = event.get("detail")
        if detail == "Missed Penalty":
            continue
        elapsed = (event.get("time") or {}).get("elapsed")
        if elapsed is None or int(elapsed) > 90:
            continue
        team = event.get("team") or {}
        team_id = _opt_int(team.get("id"))
        if team_id is None:
            continue
        player = event.get("player") or {}
        goals.append(
            GoalEvent(
                minute=int(elapsed),
                team_id=team_id,
                player_id=_opt_int(player.get("id")),
                player_name=player.get("name"),
                is_own_goal=(detail == "Own Goal"),
                is_penalty=(detail == "Penalty"),
            )
        )
    return tuple(goals)


def parse_goal_timeline(events: list[dict[str, Any]]) -> tuple[GoalEvent, ...]:
    """Full goal timeline for live notifications (§9.4): like ``parse_goals`` but **uncapped**
    (keeps extra-time goals). Excludes ``Missed Penalty`` and the penalty shootout (null elapsed).
    """
    goals: list[GoalEvent] = []
    for event in events:
        if event.get("type") != "Goal":
            continue
        detail = event.get("detail")
        if detail == "Missed Penalty":
            continue
        time_ = event.get("time") or {}
        elapsed = time_.get("elapsed")
        if elapsed is None:  # penalty shootout / malformed → not a running-score goal
            continue
        team = event.get("team") or {}
        team_id = _opt_int(team.get("id"))
        if team_id is None:
            continue
        player = event.get("player") or {}
        goals.append(
            GoalEvent(
                minute=int(elapsed),
                team_id=team_id,
                player_id=_opt_int(player.get("id")),
                player_name=player.get("name"),
                is_own_goal=(detail == "Own Goal"),
                is_penalty=(detail == "Penalty"),
                extra=_opt_int(time_.get("extra")),
            )
        )
    return tuple(goals)


def parse_var_cancellations(events: list[dict[str, Any]]) -> tuple[VarCancellation, ...]:
    """VAR events that disallowed/cancelled a goal, in API chronological order (§9.4).

    Grounded against API-Football's VAR events: the docs enumerate ``type="Var"`` detail
    ``"Goal cancelled"``, but the **live feed** also returns ``"Goal Disallowed - offside"`` (and
    other ``Goal Disallowed - <reason>`` variants) not listed in the docs — per the grounding rule,
    live docs win. We therefore match any ``Var`` event whose detail names a goal being
    cancelled/disallowed, while excluding confirmations (``Goal confirmed``) and non-goal reversals
    (``Penalty confirmed``/``Penalty cancelled``, ``Red card cancelled``).
    Doc: https://www.api-football.com/news/post/var-events
    """
    cancellations: list[VarCancellation] = []
    for event in events:
        if event.get("type") != "Var":
            continue
        detail = event.get("detail") or ""
        lowered = detail.lower()
        if not lowered.startswith("goal"):
            continue  # "Penalty …" / "Red card cancelled" → not a goal cancellation
        if "cancel" not in lowered and "disallow" not in lowered:
            continue  # "Goal confirmed" → goal stands, not a cancellation
        time_ = event.get("time") or {}
        elapsed = time_.get("elapsed")
        if elapsed is None:  # penalty shootout / malformed → not a running-score event
            continue
        team = event.get("team") or {}
        team_id = _opt_int(team.get("id"))
        if team_id is None:
            continue
        player = event.get("player") or {}
        cancellations.append(
            VarCancellation(
                minute=int(elapsed),
                team_id=team_id,
                player_name=player.get("name"),
                detail=detail,
                extra=_opt_int(time_.get("extra")),
            )
        )
    return tuple(cancellations)


def advancing_team_id(teams: dict[str, Any]) -> int | None:
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    if home.get("winner") is True:
        return _opt_int(home.get("id"))
    if away.get("winner") is True:
        return _opt_int(away.get("id"))
    return None


def map_match_result(item: dict[str, Any], events: list[dict[str, Any]]) -> MatchResult:
    """Map a ``/fixtures`` item + its ``/fixtures/events`` to a MatchResult (90′ score)."""
    fixture = item.get("fixture") or {}
    score = item.get("score") or {}
    fulltime = score.get("fulltime") or {}
    halftime = score.get("halftime") or {}
    live = item.get("goals") or {}
    return MatchResult(
        fixture_id=int(fixture["id"]),
        stage=classify_stage((item.get("league") or {}).get("round")),
        status=normalize_status((fixture.get("status") or {}).get("short", "")),
        home_goals_90=_opt_int(fulltime.get("home")),
        away_goals_90=_opt_int(fulltime.get("away")),
        goals=parse_goals(events),
        advancing_team_id=advancing_team_id(item.get("teams") or {}),
        live_home_goals=_opt_int(live.get("home")),
        live_away_goals=_opt_int(live.get("away")),
        home_goals_ht=_opt_int(halftime.get("home")),
        away_goals_ht=_opt_int(halftime.get("away")),
    )


class ApiFootballProvider:
    """``FootballProvider`` backed by API-Football v3 over httpx."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        league_id: int,
        season: int,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = _utcnow_aware,
    ) -> None:
        self._league_id = league_id
        self._season = season
        self._clock = clock
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={"x-apisports-key": api_key},
                timeout=timeout,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        errors = body.get("errors")
        if errors:
            raise ProviderError(f"API-Football error for {path}: {errors}")
        result: list[dict[str, Any]] = body.get("response") or []
        return result

    async def get_fixtures(self, window_hours: int) -> list[Fixture]:
        now = self._clock()
        cutoff = now + timedelta(hours=window_hours)
        items = await self._get(
            "/fixtures",
            {
                "league": self._league_id,
                "season": self._season,
                "from": now.date().isoformat(),
                "to": cutoff.date().isoformat(),
                "timezone": "UTC",
            },
        )
        fixtures: list[Fixture] = []
        for item in items:
            fixture = map_fixture(item)
            if fixture is None:
                continue
            if now <= fixture.kickoff_utc <= cutoff:
                fixtures.append(fixture)
        return fixtures

    async def get_live_results(self) -> list[MatchResult]:
        items = await self._get(
            "/fixtures",
            {"league": self._league_id, "season": self._season, "live": "all"},
        )
        # Live results carry status + 90′ score; the goal timeline is fetched at settlement time.
        return [map_match_result(item, []) for item in items]

    async def get_match_result(self, fixture_id: int) -> MatchResult:
        items = await self._get("/fixtures", {"id": fixture_id})
        if not items:
            raise ProviderError(f"no fixture returned for id={fixture_id}")
        events = await self._get("/fixtures/events", {"fixture": fixture_id})
        return map_match_result(items[0], events)

    async def get_goal_events(self, fixture_id: int) -> tuple[GoalEvent, ...]:
        """Full goal timeline (incl. extra time) for live notifications (§9.4)."""
        events = await self._get("/fixtures/events", {"fixture": fixture_id})
        return parse_goal_timeline(events)

    async def get_goal_cancellations(self, fixture_id: int) -> tuple[VarCancellation, ...]:
        """VAR goal-cancellation events for one fixture (live notifications, §9.4)."""
        events = await self._get("/fixtures/events", {"fixture": fixture_id})
        return parse_var_cancellations(events)
