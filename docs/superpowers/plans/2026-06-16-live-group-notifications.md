# Live Group Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post two new live messages to the Telegram group during a match — a "kickoff" message when a tracked game actually starts, and one "goal" message per goal (running score + scorer + minute, including extra time) — riding the existing live-poll job with no new cadence.

**Architecture:** The existing `bot/poll_job.py` already calls `get_live_results()` every `poll_interval_minutes` during a game's active window. We enrich that feed with the free running score (`item.goals.{home,away}`), detect kickoff from the `SCHEDULED→LIVE` transition, and detect goals from a running-score increase — spending one extra `/fixtures/events` call only when a game's score actually changed. New persisted state (`games.started_at`, `games.goals_announced`) dedups posts across polls and restarts. Grading/settlement is untouched (it keeps using the 90′ score).

**Tech Stack:** Python 3.12, python-telegram-bot 22.x (`JobQueue`), SQLAlchemy 2.0 + Alembic, httpx, pytest/pytest-asyncio. Spec: `docs/superpowers/specs/2026-06-16-live-group-notifications-design.md`.

**Gates (run before every commit):** `ruff check .` · `ruff format --check .` · `mypy --strict .` · `pytest`. All four MUST pass. Commands below assume `uv run <cmd>` per repo convention (e.g. `uv run pytest`).

---

## File map

- **Modify** `tigrinho/providers/base.py` — add `GoalEvent.extra`, `MatchResult.live_home_goals/live_away_goals`, and `FootballProvider.get_goal_events`.
- **Modify** `tigrinho/providers/api_football.py` — map live score into `MatchResult`; add `parse_goal_timeline` (uncapped) + `get_goal_events`.
- **Modify** `tigrinho/providers/fake.py` — scripted `get_goal_events`.
- **Create** `tigrinho/domain/live.py` — pure `Side`, `GoalProgress`, `goal_progression` (own-goal flip).
- **Modify** `tigrinho/domain/text_pt.py` — `kickoff_text`, `goal_minute_label`, `goal_text`.
- **Modify** `tigrinho/db/models.py` — `Game.started_at`, `Game.goals_announced`.
- **Create** `tigrinho/db/migrations/versions/d2e3f4a5b6c7_add_game_live_notifications.py`.
- **Modify** `tigrinho/bot/poll_job.py` — kickoff + goal detection, `_post_to_group`, `_announce_goals`.
- **Modify** `tests/test_api_football.py`, `tests/test_fake_provider.py`, `tests/test_text_pt.py`, `tests/test_models.py`, `tests/test_poll_job.py`; **create** `tests/test_live.py`.
- **Modify** `COMPLETION.md` (+§9.4, §7.1 note), `PROGRESS.md` (log entry).

---

## Task 1: Provider value objects + live-score mapping

**Files:**
- Modify: `tigrinho/providers/base.py`
- Modify: `tigrinho/providers/api_football.py:160-172` (`map_match_result`)
- Test: `tests/test_api_football.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_api_football.py`:

```python
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
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_api_football.py::test_get_live_results_carries_live_score -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'live_home_goals'` is not raised yet; instead `AttributeError: 'MatchResult' object has no attribute 'live_home_goals'`.

- [ ] **Step 3: Add the value-object fields** in `tigrinho/providers/base.py`.

In `GoalEvent` (after `is_penalty`):

```python
    is_penalty: bool
    extra: int | None = None  # added stoppage minutes (e.g. 90+`extra`); for live display
```

In `MatchResult` (after `advancing_team_id`):

```python
    advancing_team_id: int | None
    live_home_goals: int | None = None  # current running score (incl. ET); from item.goals
    live_away_goals: int | None = None
```

- [ ] **Step 4: Populate the live score** in `tigrinho/providers/api_football.py` `map_match_result`. Replace the function body's `return MatchResult(...)` so it reads `item.goals`:

```python
def map_match_result(item: dict[str, Any], events: list[dict[str, Any]]) -> MatchResult:
    """Map a ``/fixtures`` item + its ``/fixtures/events`` to a MatchResult (90′ score)."""
    fixture = item.get("fixture") or {}
    fulltime = (item.get("score") or {}).get("fulltime") or {}
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
    )
```

- [ ] **Step 5: Run it — expect PASS**

Run: `uv run pytest tests/test_api_football.py::test_get_live_results_carries_live_score -v`
Expected: PASS.

- [ ] **Step 6: Run the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green (existing `MatchResult`/`GoalEvent` constructions still valid — new fields are defaulted).

- [ ] **Step 7: Commit**

```bash
git add tigrinho/providers/base.py tigrinho/providers/api_football.py tests/test_api_football.py
git commit -m "feat(provider): carry live running score on MatchResult + GoalEvent.extra"
```

---

## Task 2: `get_goal_events` (uncapped timeline) across the interface

**Files:**
- Modify: `tigrinho/providers/base.py` (Protocol)
- Modify: `tigrinho/providers/api_football.py` (`parse_goal_timeline`, `get_goal_events`)
- Modify: `tigrinho/providers/fake.py`
- Test: `tests/test_api_football.py`, `tests/test_fake_provider.py`

- [ ] **Step 0: Ground the API surface (MANDATORY, CLAUDE.md §2).** Web-search the current API-Football v3 docs and confirm: `/fixtures?live=all` items include `goals.{home,away}`; `/fixtures/events` items carry `time.{elapsed,extra}`, `type`, `detail` (`Normal Goal`/`Own Goal`/`Penalty`/`Missed Penalty`), and how **penalty-shootout** goals are represented (expected: `time.elapsed` is null). The module docstring at `tigrinho/providers/api_football.py:1-22` already records these shapes (verified June 2026) — update it only if the live docs disagree, and keep the doc URL in the comment. If shootout events carry a non-null `elapsed`, adjust the `elapsed is None` filter in Step 3 accordingly and note it.

- [ ] **Step 1: Write the failing test** — append to `tests/test_api_football.py`:

```python
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
```

Update the import line at the top of `tests/test_api_football.py` to include `parse_goal_timeline` (add it next to the existing `parse_goals` import).

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_api_football.py::test_parse_goal_timeline_keeps_extra_time_and_flags tests/test_api_football.py::test_get_goal_events_calls_events_endpoint -v`
Expected: FAIL — `ImportError: cannot import name 'parse_goal_timeline'`.

- [ ] **Step 3: Add `parse_goal_timeline` + `get_goal_events`** in `tigrinho/providers/api_football.py`. Add the function right after `parse_goals` (after line 147):

```python
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
```

Then add the provider method to `ApiFootballProvider`, after `get_match_result` (after line 252):

```python
    async def get_goal_events(self, fixture_id: int) -> tuple[GoalEvent, ...]:
        """Full goal timeline (incl. extra time) for live notifications (§9.4)."""
        events = await self._get("/fixtures/events", {"fixture": fixture_id})
        return parse_goal_timeline(events)
```

- [ ] **Step 4: Add the Protocol method** in `tigrinho/providers/base.py`, inside `FootballProvider`, after `get_match_result`:

```python
    async def get_goal_events(self, fixture_id: int) -> tuple[GoalEvent, ...]:
        """Full goal timeline (incl. extra time) for live notifications (§9.4)."""
        ...
```

- [ ] **Step 5: Write the FakeProvider test** — append to `tests/test_fake_provider.py`:

```python
async def test_fake_provider_scripts_goal_events() -> None:
    goal = GoalEvent(
        minute=23,
        team_id=10,
        player_id=100,
        player_name="Vini",
        is_own_goal=False,
        is_penalty=False,
    )
    provider = FakeProvider(goal_events={1001: (goal,)})
    events = await provider.get_goal_events(1001)
    assert [g.player_name for g in events] == ["Vini"]
    assert await provider.get_goal_events(9999) == ()  # unknown fixture → empty
    assert provider.call_log == ["get_goal_events:1001", "get_goal_events:9999"]
```

- [ ] **Step 6: Run it — expect FAIL**

Run: `uv run pytest tests/test_fake_provider.py::test_fake_provider_scripts_goal_events -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'goal_events'`.

- [ ] **Step 7: Implement FakeProvider** in `tigrinho/providers/fake.py`. Update the import line and the class:

```python
from tigrinho.providers.base import Fixture, FixtureSeq, GoalEvent, MatchResult, ResultSeq


class FakeProvider:
    """In-memory :class:`~tigrinho.providers.base.FootballProvider` implementation."""

    def __init__(
        self,
        *,
        fixtures: FixtureSeq | None = None,
        results: ResultSeq | None = None,
        goal_events: dict[int, tuple[GoalEvent, ...]] | None = None,
    ) -> None:
        self._fixtures: list[Fixture] = list(fixtures or [])
        self._results: dict[int, MatchResult] = {r.fixture_id: r for r in (results or [])}
        self._goal_events: dict[int, tuple[GoalEvent, ...]] = dict(goal_events or {})
        self.call_log: list[str] = []
```

Add the method after `get_match_result`:

```python
    async def get_goal_events(self, fixture_id: int) -> tuple[GoalEvent, ...]:
        self.call_log.append(f"get_goal_events:{fixture_id}")
        return self._goal_events.get(fixture_id, ())
```

- [ ] **Step 8: Run the new tests + the protocol test — expect PASS**

Run: `uv run pytest tests/test_api_football.py tests/test_fake_provider.py -v`
Expected: PASS, including `test_fake_provider_satisfies_protocol` (FakeProvider now has `get_goal_events`).

- [ ] **Step 9: Run the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add tigrinho/providers/base.py tigrinho/providers/api_football.py tigrinho/providers/fake.py tests/test_api_football.py tests/test_fake_provider.py
git commit -m "feat(provider): get_goal_events — full goal timeline incl. extra time"
```

---

## Task 3: Pure goal progression (`domain/live.py`)

**Files:**
- Create: `tigrinho/domain/live.py`
- Test: `tests/test_live.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_live.py`:

```python
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


def test_goal_progression_own_goal_credits_opponent() -> None:
    # Away team (20) puts it in their own net -> credited to home (10).
    prog = goal_progression(10, 20, [_g(30, 20, own=True)])
    assert (prog[0].home_score, prog[0].away_score) == (1, 0)
    assert prog[0].scoring_side is Side.HOME


def test_goal_progression_extra_time_preserved() -> None:
    prog = goal_progression(10, 20, [_g(105, 10, extra=2)])
    assert prog[0].goal.minute == 105
    assert prog[0].goal.extra == 2
    assert prog[0].home_score == 1


def test_goal_progression_empty() -> None:
    assert goal_progression(10, 20, []) == []
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_live.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tigrinho.domain.live'`.

- [ ] **Step 3: Implement** — create `tigrinho/domain/live.py`:

```python
"""Pure live-goal helpers for in-match group notifications (COMPLETION.md §9.4).

Walks a goal timeline and reconstructs the running score, applying the **own-goal flip** (an own
goal counts for the *opposing* side). No I/O, clock, or DB — deterministic and testable.
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
    """Reconstruct the running score for ``goals`` (chronological), own-goal flip applied."""
    home = 0
    away = 0
    out: list[GoalProgress] = []
    for goal in goals:
        scored_for_home = goal.team_id == home_team_id
        if goal.is_own_goal:
            scored_for_home = not scored_for_home
        if scored_for_home:
            home += 1
            side = Side.HOME
        else:
            away += 1
            side = Side.AWAY
        out.append(GoalProgress(goal=goal, home_score=home, away_score=away, scoring_side=side))
    return out
```

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_live.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tigrinho/domain/live.py tests/test_live.py
git commit -m "feat(domain): pure goal_progression with own-goal flip"
```

---

## Task 4: pt-BR messages (`text_pt`)

**Files:**
- Modify: `tigrinho/domain/text_pt.py`
- Test: `tests/test_text_pt.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_text_pt.py`:

```python
def test_kickoff_text() -> None:
    t = kickoff_text("Brasil", "Argentina")
    assert "Bola rolando" in t
    assert "Brasil x Argentina" in t


def test_goal_text_basic() -> None:
    t = goal_text(
        scoring_team="Brasil",
        home_team="Brasil",
        away_team="Argentina",
        home_score=1,
        away_score=0,
        minute=23,
        extra=None,
        scorer="Vini Jr",
        is_penalty=False,
        is_own_goal=False,
    )
    assert "GOL do Brasil" in t
    assert "Brasil 1 x 0 Argentina" in t
    assert "Vini Jr" in t
    assert "(23')" in t


def test_goal_text_penalty_and_stoppage() -> None:
    t = goal_text(
        scoring_team="Brasil",
        home_team="Brasil",
        away_team="Argentina",
        home_score=1,
        away_score=0,
        minute=90,
        extra=3,
        scorer="Neymar",
        is_penalty=True,
        is_own_goal=False,
    )
    assert "pênalti" in t
    assert "90+3'" in t


def test_goal_text_own_goal_without_scorer() -> None:
    t = goal_text(
        scoring_team="Brasil",
        home_team="Brasil",
        away_team="Argentina",
        home_score=1,
        away_score=0,
        minute=45,
        extra=None,
        scorer=None,
        is_own_goal=True,
        is_penalty=False,
    )
    assert "gol contra" in t
    assert "—" not in t  # no scorer dash when the provider gives no name


def test_goal_text_escapes_html() -> None:
    t = goal_text(
        scoring_team="A&B",
        home_team="A&B",
        away_team="C<D",
        home_score=0,
        away_score=1,
        minute=5,
        extra=None,
        scorer="x<y",
        is_penalty=False,
        is_own_goal=False,
    )
    assert "&amp;" in t
    assert "&lt;" in t
```

Add `goal_text` and `kickoff_text` to the existing `from tigrinho.domain.text_pt import (...)` block in that test file.

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_text_pt.py::test_kickoff_text tests/test_text_pt.py::test_goal_text_basic -v`
Expected: FAIL — `ImportError: cannot import name 'kickoff_text'`.

- [ ] **Step 3: Implement** — add to `tigrinho/domain/text_pt.py` (near the other builders, e.g. after `results_text`):

```python
def kickoff_text(home_team: str, away_team: str) -> str:
    """Group post when a tracked game kicks off (§9.4)."""
    return (
        f"🔥 <b>Bola rolando!</b> {escape(home_team)} x {escape(away_team)} "
        "— boa sorte, Tigrinhos! 🐯"
    )


def goal_minute_label(minute: int, extra: int | None) -> str:
    """Render a goal minute, e.g. ``23'`` or ``90+3'`` (§9.4)."""
    return f"{minute}+{extra}'" if extra else f"{minute}'"


def goal_text(
    *,
    scoring_team: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    minute: int,
    extra: int | None,
    scorer: str | None,
    is_penalty: bool,
    is_own_goal: bool,
) -> str:
    """Group post for one goal: running score + scorer + minute (§9.4)."""
    tags: list[str] = []
    if is_penalty:
        tags.append("pênalti")
    if is_own_goal:
        tags.append("gol contra")
    detail = goal_minute_label(minute, extra)
    if tags:
        detail = f"{', '.join(tags)}, {detail}"
    scorer_part = f" — {escape(scorer)}" if scorer else ""
    return (
        f"⚽ <b>GOL do {escape(scoring_team)}!</b> "
        f"{escape(home_team)} {home_score} x {away_score} {escape(away_team)}"
        f"{scorer_part} ({detail})"
    )
```

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_text_pt.py -v -k "kickoff or goal_text"`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tigrinho/domain/text_pt.py tests/test_text_pt.py
git commit -m "feat(text): pt-BR kickoff + goal messages"
```

---

## Task 5: DB columns + migration

**Files:**
- Modify: `tigrinho/db/models.py` (`Game`)
- Create: `tigrinho/db/migrations/versions/d2e3f4a5b6c7_add_game_live_notifications.py`
- Test: `tests/test_models.py` (+ existing `tests/test_migrations.py` validates the schema match)

- [ ] **Step 1: Write the failing test** — append to `tests/test_models.py`:

```python
def test_game_live_notification_defaults(session: Session) -> None:
    _player, game = _seed_player_and_game(session)
    stored = session.get(Game, game.fixture_id)
    assert stored is not None
    assert stored.started_at is None
    assert stored.goals_announced == 0
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_models.py::test_game_live_notification_defaults -v`
Expected: FAIL — `AttributeError: 'Game' object has no attribute 'started_at'`.

- [ ] **Step 3: Add the columns** in `tigrinho/db/models.py`, in `Game`, right after `reminded_at`:

```python
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    goals_announced: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
```

(`Integer` and `DateTime` are already imported in this module.)

- [ ] **Step 4: Run the model test — expect PASS**

Run: `uv run pytest tests/test_models.py::test_game_live_notification_defaults -v`
Expected: PASS (tests use `Base.metadata.create_all`, so no migration needed for this one).

- [ ] **Step 5: Confirm the migration test now FAILS** (ORM has columns the migrations lack):

Run: `uv run pytest tests/test_migrations.py::test_upgrade_head_matches_orm_metadata -v`
Expected: FAIL — migrated `games` columns don't include `started_at`/`goals_announced`.

- [ ] **Step 6: Create the migration** — `tigrinho/db/migrations/versions/d2e3f4a5b6c7_add_game_live_notifications.py`:

```python
"""add games.started_at + games.goals_announced (live notifications)

Revision ID: d2e3f4a5b6c7
Revises: c1a2b3d4e5f6
Create Date: 2026-06-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1a2b3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("goals_announced", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.drop_column("goals_announced")
        batch_op.drop_column("started_at")
```

- [ ] **Step 7: Run the migration tests — expect PASS**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS — `test_upgrade_head_matches_orm_metadata` now matches; downgrade round-trip clean.

- [ ] **Step 8: Run the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add tigrinho/db/models.py tigrinho/db/migrations/versions/d2e3f4a5b6c7_add_game_live_notifications.py tests/test_models.py
git commit -m "feat(db): games.started_at + goals_announced (+ migration)"
```

---

## Task 6: Poll-job wiring (kickoff + goals)

**Files:**
- Modify: `tigrinho/bot/poll_job.py`
- Test: `tests/test_poll_job.py`

This is the integration task. Implement the helpers + the live-poll branch changes first (one code edit), then the tests use the new behavior. Because several tests share fixtures, write the helpers, then add tests, then make them pass.

- [ ] **Step 1: Add imports + helpers** in `tigrinho/bot/poll_job.py`.

Extend the `text_pt` import to add the new builders, and import the domain helper:

```python
from tigrinho.domain.live import Side, goal_progression
from tigrinho.domain.text_pt import (
    CATEGORY_LABELS,
    escape,
    goal_text,
    kickoff_text,
    results_text,
)
```

Add these two helpers near `_settle_and_announce`:

```python
async def _post_to_group(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, text: str, *, what: str
) -> None:
    """Best-effort live group post; on failure log + DM admin, never crash the bot (§14)."""
    try:
        await context.bot.send_message(
            chat_id=app_context.settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except TelegramError as exc:
        _log.error("group_post_failed", what=what, error=str(exc))
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Falha ao postar {what} no grupo: <code>{escape(str(exc))}</code>",
        )


async def _announce_goals(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, fixture_id: int
) -> None:
    """Fetch the goal timeline and post one message per *new* goal (§9.4)."""
    events = await app_context.budget.guarded(
        lambda: app_context.provider.get_goal_events(fixture_id)
    )
    messages: list[str] = []
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None:
            return
        progression = goal_progression(game.home_team_id, game.away_team_id, events)
        for prog in progression[game.goals_announced :]:
            scoring_team = (
                game.home_team_name if prog.scoring_side is Side.HOME else game.away_team_name
            )
            messages.append(
                goal_text(
                    scoring_team=scoring_team,
                    home_team=game.home_team_name,
                    away_team=game.away_team_name,
                    home_score=prog.home_score,
                    away_score=prog.away_score,
                    minute=prog.goal.minute,
                    extra=prog.goal.extra,
                    scorer=prog.goal.player_name,
                    is_penalty=prog.goal.is_penalty,
                    is_own_goal=prog.goal.is_own_goal,
                )
            )
        game.goals_announced = len(progression)
        session.commit()

    for message in messages:
        await _post_to_group(app_context, context, message, what="um gol")
```

- [ ] **Step 2: Rewrite the live-poll branch** in `_run_poll`. Replace the current block (the comment `# (2) Lower-priority live poll...` down through the `for fixture_id in finished:` loop, `tigrinho/bot/poll_job.py:95-113`) with:

```python
    # (2) Lower-priority live poll for in-progress games: status, kickoff + goal posts (§9.4),
    #     and early finishes.
    live = await app_context.budget.guarded(app_context.provider.get_live_results)
    live_by_id = {result.fixture_id: result for result in live}
    finished: list[int] = []
    kickoffs: list[tuple[str, str]] = []
    goal_fixtures: list[int] = []
    with app_context.session_factory() as session:
        games = GameRepository(session)
        for fixture_id in in_progress:
            game = games.get(fixture_id)
            result = live_by_id.get(fixture_id)
            if game is None or result is None:
                continue
            if result.status is GameStatus.FINISHED:
                finished.append(fixture_id)
                continue
            if result.status is not GameStatus.LIVE:
                continue
            if game.status is not GameStatus.LIVE:
                game.status = GameStatus.LIVE
            if game.started_at is None:
                game.started_at = now  # kickoff detected this cycle
                kickoffs.append((game.home_team_name, game.away_team_name))
            # Goals only after kickoff is recorded; same-cycle catch-up is fine (started_at set above).
            live_total = (result.live_home_goals or 0) + (result.live_away_goals or 0)
            if live_total > game.goals_announced:
                goal_fixtures.append(fixture_id)
            elif live_total < game.goals_announced:
                game.goals_announced = live_total  # VAR disallowed a goal — resync, post nothing
        session.commit()

    for home, away in kickoffs:
        await _post_to_group(app_context, context, kickoff_text(home, away), what="o início do jogo")

    for fixture_id in goal_fixtures:
        await _announce_goals(app_context, context, fixture_id)

    for fixture_id in finished:
        await _settle_and_announce(app_context, context, fixture_id)
```

(`now` is the `now = utcnow()` already defined at the top of `_run_poll`.)

- [ ] **Step 3: Run the gates to confirm no regression**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest tests/test_poll_job.py`
Expected: existing poll tests PASS (the live-poll loop still flips status + collects finished; new behavior is dormant when no `started_at`/live score is involved). Fix any formatting/typing nits before moving on.

- [ ] **Step 4: Add test fixtures + the new tests** — append to `tests/test_poll_job.py`. First add helpers near the top (after `_finished_result`):

```python
def _seed_live_game(
    session_factory: sessionmaker[Session],
    *,
    fixture_id: int = 1001,
    started: bool = True,
    goals_announced: int = 0,
    hours_ago: float = 0.5,
) -> None:
    kickoff = _now() - timedelta(hours=hours_ago)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash="h",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.LIVE if started else GameStatus.SCHEDULED,
                started_at=_now() if started else None,
                goals_announced=goals_announced,
            )
        )
        session.commit()


def _live_result(
    *, fixture_id: int = 1001, home: int = 0, away: int = 0, status: GameStatus = GameStatus.LIVE
) -> MatchResult:
    return MatchResult(
        fixture_id=fixture_id,
        stage=Stage.GROUP,
        status=status,
        home_goals_90=None,
        away_goals_90=None,
        goals=(),
        advancing_team_id=None,
        live_home_goals=home,
        live_away_goals=away,
    )


def _goal(minute: int, team_id: int, *, name: str | None = None, own: bool = False) -> GoalEvent:
    return GoalEvent(
        minute=minute,
        team_id=team_id,
        player_id=None,
        player_name=name,
        is_own_goal=own,
        is_penalty=False,
    )


def _sent_texts(bot: AsyncMock) -> list[str]:
    return [call.kwargs["text"] for call in bot.send_message.await_args_list]
```

Then add the tests:

```python
async def test_kickoff_announced_once(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=False, goals_announced=0)  # SCHEDULED, not started
    provider = FakeProvider(results=[_live_result(home=0, away=0)])
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)
    assert any("Bola rolando" in t for t in _sent_texts(bot))
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.started_at is not None

    bot.send_message.reset_mock()
    await poll_job(context)  # second cycle: already started, score still 0-0
    assert not any("Bola rolando" in t for t in _sent_texts(bot))


async def test_kickoff_not_announced_when_first_seen_finished(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=False)  # SCHEDULED
    provider = FakeProvider(results=[_finished_result()])  # feed reports FINISHED
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)
    assert not any("Bola rolando" in t for t in _sent_texts(bot))
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.started_at is None


async def test_goal_announced_on_score_increase(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=0)
    provider = FakeProvider(
        results=[_live_result(home=1, away=0)],
        goal_events={1001: (_goal(23, 10, name="Vini Jr"),)},
    )
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    texts = _sent_texts(bot)
    assert any("GOL do Brasil" in t and "1 x 0" in t and "Vini Jr" in t for t in texts)
    assert "get_goal_events:1001" in provider.call_log
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 1


async def test_no_event_fetch_when_score_unchanged(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=1)
    provider = FakeProvider(results=[_live_result(home=1, away=0)])  # total 1 == goals_announced
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    assert not any(c.startswith("get_goal_events") for c in provider.call_log)
    assert not any("GOL" in t for t in _sent_texts(bot))


async def test_var_disallowed_goal_resyncs_without_posting(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=2)
    provider = FakeProvider(results=[_live_result(home=1, away=0)])  # total 1 < goals_announced 2
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    assert not any(c.startswith("get_goal_events") for c in provider.call_log)
    assert not any("GOL" in t for t in _sent_texts(bot))
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 1


async def test_multiple_new_goals_posted_in_order(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed_live_game(session_factory, started=True, goals_announced=0)
    provider = FakeProvider(
        results=[_live_result(home=1, away=1)],
        goal_events={1001: (_goal(10, 10, name="Home One"), _goal(25, 20, name="Away One"))},
    )
    app_context = _app_context(settings, session_factory, provider)
    context, bot = _context(app_context)

    await poll_job(context)

    goal_texts = [t for t in _sent_texts(bot) if "GOL" in t]
    assert len(goal_texts) == 2
    assert "1 x 0" in goal_texts[0]
    assert "1 x 1" in goal_texts[1]
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.goals_announced == 2
```

- [ ] **Step 5: Run the new tests — expect PASS**

Run: `uv run pytest tests/test_poll_job.py -v`
Expected: PASS (existing + 6 new tests).

- [ ] **Step 6: Run the gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green. Domain coverage for `scoring.py`/`settlement.py` unchanged at 100% (this task adds no branches there).

- [ ] **Step 7: Commit**

```bash
git add tigrinho/bot/poll_job.py tests/test_poll_job.py
git commit -m "feat(poll): live kickoff + goal group notifications"
```

---

## Task 7: Spec/docs maintenance (COMPLETION.md §9.4, PROGRESS.md)

**Files:**
- Modify: `COMPLETION.md`
- Modify: `PROGRESS.md`

No command/category/scoring/grading change → **`/ajuda` is untouched** (the §11 maintenance trigger does not fire). No new config → README/`.env`/`config.yaml` unchanged.

- [ ] **Step 1: Add COMPLETION.md §9.4.** Locate the §9.3 pre-game-reminder subsection and insert a new subsection after it:

```markdown
### §9.4 — Live group notifications (kickoff + goals)

The live-poll job (§9.2) also posts two in-match group messages, riding the same
`poll_interval_minutes` cadence (no separate job, no extra polling):

- **Kickoff.** When `get_live_results()` first reports a tracked game `LIVE`, post a "Bola rolando"
  message and set `games.started_at` (dedups across polls + restarts). Skipped for a game first seen
  already `FINISHED` (e.g. downtime through the match); the settlement results post covers it.
- **Goals.** The live feed carries the running score (`MatchResult.live_home_goals/live_away_goals`
  from `item.goals`). When a started game's running total exceeds `games.goals_announced`, one
  budgeted `get_goal_events(fixture_id)` call fetches the **uncapped** goal timeline (incl. extra
  time; penalty shootout excluded); each new goal is posted with the running score, scorer, and
  minute (`(pênalti)`/`(gol contra)` tags; own goals credited to the opposing side). The cursor
  `goals_announced` advances to the timeline length. A VAR-disallowed goal (running total drops)
  resyncs the cursor down and posts nothing. Goal cost is ~1 events call per actual goal — cycles
  with no goal cost nothing beyond the live feed already fetched.

Provider additions (§7.1): `MatchResult.live_home_goals/live_away_goals`, `GoalEvent.extra`, and
`FootballProvider.get_goal_events(fixture_id) -> tuple[GoalEvent, ...]`. Grading/settlement is
unchanged — it still uses the 90′ regulation score and the ≤90′ timeline.
```

- [ ] **Step 2: Add a one-line §7.1 note.** In the §7.1 value-object descriptions, add a sentence noting `MatchResult` now also carries the live running score (`live_home_goals`/`live_away_goals`) used by §9.4, and `GoalEvent` carries optional `extra` (stoppage minutes); both are display-only and do not affect grading.

- [ ] **Step 3: Append a PROGRESS.md log entry** under the dated log:

```markdown
### 2026-06-16 — Feature: live group notifications (kickoff + goals) (§9.4)

User request, built in an isolated worktree. The live-poll job now posts a "Bola rolando" message
when a tracked game goes LIVE and one message per goal (running score + scorer + minute, incl. extra
time), gated on a free running-score check so the `/fixtures/events` endpoint is hit only when a
game actually scores. New provider surface (`MatchResult.live_*`, `GoalEvent.extra`,
`get_goal_events`), pure `domain/live.goal_progression` (own-goal flip), pt-BR `kickoff_text`/
`goal_text`, new `games.started_at` + `games.goals_announced` columns + append-only migration
`d2e3f4a5b6c7`. Grading/settlement untouched; `/ajuda` unaffected (no command/category/scoring
change). Spec + plan under `docs/superpowers/`. All four gates green.
```

- [ ] **Step 4: Run the gates** (docs-only, but confirm nothing else drifted)

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add COMPLETION.md PROGRESS.md
git commit -m "docs: record live group notifications (COMPLETION §9.4 + PROGRESS)"
```

---

## Final verification

- [ ] **Full gate sweep:** `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest` — all green.
- [ ] **Migrations apply cleanly:** `uv run alembic upgrade head` against a scratch DB, then confirm `tests/test_migrations.py` passes (head matches ORM; downgrade clean).
- [ ] **Manual trace (FakeProvider):** kickoff posts once; goal posts on score increase; no events call when score is flat; VAR resync posts nothing; restart (re-run poll) posts no duplicates.
- [ ] **Spec coverage:** kickoff trigger (real LIVE) ✓ · `poll_interval_minutes` cadence ✓ · score+scorer+minute detail ✓ · all goals incl. extra time ✓ · own-goal flip ✓ · VAR resync ✓ · restart-safety ✓ · best-effort group send ✓.
```
