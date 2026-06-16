# Design — Live group notifications (kickoff + goals)

**Date:** 2026-06-16
**Status:** Approved
**Spec home:** extends `COMPLETION.md` §9 (new §9.4) and §7.1/§7.2 (provider value objects).

## Goal

Post two new live messages to the **group** during a match:

1. **Kickoff** — "the ball is rolling" when a tracked game actually starts.
2. **Goal** — one message per goal, with the running score, scorer, and minute.

Both ride the **existing live-poll job** (`bot/poll_job.py`), which already runs every
`poll_interval_minutes` during a game's active window and makes one `get_live_results()` call per
cycle. No new job and no new cadence — goal freshness is bounded by `poll_interval_minutes` (default
10 min), as requested.

### Clarified semantics (chosen with the user)

- **Kickoff trigger = real kickoff**, detected when API-Football first reports the match `LIVE` —
  not a clock-based post at the scheduled time. Accurate to the actual start; lags up to one poll
  interval.
- **Goal cadence = `poll_interval_minutes`.** No separate, faster poll. Goals are announced on the
  next cycle after they happen.
- **Goal detail = score + scorer + minute**, with `(pênalti)` / `(gol contra)` tags. Scorer name is
  omitted when the provider supplies none.
- **Goal scope = all goals incl. extra time** (regulation + ET, penalties, own goals). The penalty
  **shootout** is excluded (it is a tiebreak, not added to the running score). This requires
  extending the provider, which today parses only ≤90′ goals (for grading).

## Non-goals

- No change to **grading / settlement** — those keep using the 90′ regulation score
  (`home_goals_90`) and the existing ≤90′ goal timeline. Live notifications are display-only.
- No half-time / full-time messages. Full-time is already covered by the existing settlement
  **results post**.
- No config on/off toggle (YAGNI). Can be added later if the group wants a kill-switch.

## The constraint that drives the design

`get_live_results()` maps each live fixture with **empty events** on purpose
(`map_match_result(item, [])`), so the live feed carries **status + 90′ score but no goal
timeline**. Within that same feed item, however:

- `item.goals.{home,away}` is the **current running score** — uncapped (counts extra time) and
  already attributes own goals to the correct side. **Free**, no extra call.
- Scorer **names + minutes** live only in `/fixtures/events?fixture=<id>` — a **separate** call.

So: detect *that* a goal happened from the free running score; spend an events call only to learn
*who/when*.

## Approach (score-gated events fetch)

Each poll cycle, inside the existing live-poll branch, after the single `get_live_results()` call:

**Kickoff.** For each in-progress game in the feed: if `started_at IS NULL` and the feed reports it
`LIVE`, post the kickoff message and set `started_at`. (Skipped when a game is first seen already
`FINISHED` — e.g. the bot was down through the match — so no stale "it's starting" post.)

**Goals.** Only for games we have posted kickoff for (`started_at IS NOT NULL`) — so a game first
seen `FINISHED` never retroactively dumps its goals; the settlement results post covers it. Kickoff
runs first in the same cycle, so a game first seen `LIVE` already a few minutes in posts kickoff and
then catches up on any goals it missed in the **same** cycle. Let
`T = live_home_goals + live_away_goals` from the feed. Compare to the persisted `goals_announced`
cursor:
- `T > goals_announced` → **one** budgeted `get_goal_events(fixture_id)` call (uncapped timeline);
  announce goals `[goals_announced:]` in order; set `goals_announced = len(timeline)`.
- `T == goals_announced` → nothing (no events call — the cheap gate).
- `T < goals_announced` → a goal was disallowed (VAR); resync `goals_announced = T`, post nothing.

Goals are rare (~2–3 per match), so the events endpoint is touched only ~2–3 times per game across
its whole window — well under `api_daily_cap` (default 100). Every cycle with no goal costs **zero**
extra calls beyond the live feed that already runs.

### Alternatives rejected

- **Always fetch events for every in-progress game each poll.** No gate needed, but N×~18 calls per
  match window even when nothing happens; during the group stage several games run at once. Wasteful.
- **Separate, faster goal-poll job.** The user required `poll_interval_minutes`; a second cadence is
  out.
- **Clock-based kickoff post at scheduled time.** Fires before the match truly starts when the
  broadcast/kickoff is delayed; the user chose real-kickoff detection.

## Components

### Provider layer (`tigrinho/providers/`)

**`base.py`**
- Add `extra: int | None = None` to `GoalEvent` (stoppage display, e.g. `90+3′`). Default keeps every
  existing construction valid.
- Extend `MatchResult` with `live_home_goals: int | None` and `live_away_goals: int | None` — the
  current running score from `item.goals`. `home_goals_90` / `away_goals_90` (grading) unchanged.
- Add to the `FootballProvider` Protocol:
  `async def get_goal_events(self, fixture_id: int) -> tuple[GoalEvent, ...]` — the **uncapped**
  chronological goal timeline for notifications.

**`api_football.py`**
- `map_match_result` reads `item.goals.{home,away}` into the new `live_*` fields (one-line addition;
  `get_live_results` now carries the running score for free).
- New `parse_goal_timeline(events)`: like `parse_goals` but **without** the `elapsed <= 90` cap;
  still drops `Missed Penalty`; excludes shootout penalties (`time.elapsed` null / shootout marker);
  populates `extra` from `time.extra`. Keep the existing `parse_goals` (≤90′) for grading untouched.
- `get_goal_events(fixture_id)` → one `GET /fixtures/events?fixture=<id>` → `parse_goal_timeline`.
- **Grounding (MANDATORY, §2):** before writing, re-verify against current API-Football v3 docs the
  `/fixtures` `goals.{home,away}` shape and the `/fixtures/events` `time.{elapsed,extra}`,
  `type`/`detail` (`Goal`/`Own Goal`/`Penalty`/`Missed Penalty`) and shootout representation. Record
  the doc URL next to the code.

**`fake.py`** — `get_live_results` already returns scripted `MatchResult`s (now carrying `live_*`).
Add `get_goal_events`: a scripted `dict[int, tuple[GoalEvent, ...]]` replayed per fixture, logged in
`call_log` (so tests can assert the gate suppressed/triggered the call).

### Domain (`tigrinho/domain/live.py`, PURE)

`goal_progression(home_team_id, away_team_id, goals) -> list[GoalProgress]` walks the timeline once,
applying the **own-goal flip** (an own goal credits the opposing side), and returns for each goal:
the `GoalEvent`, the `(home, away)` score *after* it, and the scoring `Side` (HOME/AWAY). Pure, no
I/O/clock/DB; unit-tested. The poll slices this at `[goals_announced:]` to render only new goals.

### Persistence (`tigrinho/db/`)

Two columns on `games` (mirroring the `announced_at` / `reminded_at` pattern) + one append-only
migration, `down_revision = "c1a2b3d4e5f6"` (current head):
- `started_at: Mapped[datetime | None]` (nullable) — kickoff-post dedup; restart-safe.
- `goals_announced: Mapped[int]` (default `0`, not null) — goals-posted cursor + the cheap gate.

No new repository methods are required: the poll already loads the `Game` and mutates it in-session
(e.g. `game.status = LIVE`), so it sets `game.started_at` / `game.goals_announced` and commits the
same way. `sync_fixtures` reschedule/un-void only touches `SCHEDULED`/`VOID` games, which can't have
`started_at`/`goals_announced` set, so no reset path is needed.

### Poll job (`tigrinho/bot/poll_job.py`)

In `_run_poll`, the live-poll branch (the block that already fetches `get_live_results()` and flips
status to `LIVE`) gains, per in-progress fixture present in the feed:
1. Kickoff post + `started_at` (as above).
2. Score-gate → optional budgeted `get_goal_events` → `goal_progression` → one group message per new
   goal → update `goals_announced`.

Both run **inside the existing best-effort wrapper**: a failed group send logs + DMs the admin and
never kills the bot (§14); `BudgetExceeded` from the events call surfaces via the existing
`alert_cap_reached` path. Status flips and cursor updates are committed in the same session block as
today.

### Messages (`tigrinho/domain/text_pt.py`, pt-BR / HTML)

- `kickoff_text(home, away)` → e.g. `🔥 Bola rolando! <b>Brasil x Argentina</b> — boa sorte,
  Tigrinhos! 🐯` (names HTML-escaped).
- `goal_text(scoring_team, home, away, home_score, away_score, scorer, minute_label, is_penalty,
  is_own_goal)` → e.g. `⚽ GOL do <b>Brasil</b>! Brasil 1 x 0 Argentina — Vini Jr (23′)`; appends
  `(pênalti)` / `(gol contra)`; omits ` — <scorer>` when the name is absent. `minute_label` renders
  `f"{minute}+{extra}′"` when `extra` is set, else `f"{minute}′"`.

## Data flow (one cycle)

```
poll_job (every poll_interval_minutes)
  → settle overdue (>2h) games            [unchanged]
  → if in-progress games:
      live = get_live_results()           [1 budgeted call, now also carries live_* score]
      for each in-progress fixture in live:
        if started_at is None and status == LIVE:
            send kickoff_text → group; started_at = now
        if started_at is None: continue        # never seen LIVE → no goal backfill
        T = live_home + live_away
        if T > goals_announced:
            events = get_goal_events(fid)  [1 budgeted call — only on a real score change]
            for g in goal_progression(...)[goals_announced:]:
                send goal_text → group
            goals_announced = len(events)
        elif T < goals_announced:
            goals_announced = T            [VAR resync, no post]
        if status FINISHED: → settle (results post)   [unchanged]
      commit
```

## Edge cases

- **Multiple goals in one interval** — announced in chronological order, score progressing.
- **Own goal** — credited to the correct side by the flip; scorer tagged `(gol contra)`.
- **Restart mid-match** — `started_at` and `goals_announced` are persisted → no duplicate kickoff or
  goal posts.
- **Bot down through a match** — game first seen `FINISHED`: no kickoff post, and goals are gated on
  `started_at`, so no retroactive goal dump either; the settlement results post still covers the
  final score.
- **First sighting a few minutes into a live match** — kickoff posts, then the same cycle catches up
  on any goals already scored (`goals_announced` starts at 0), in order.
- **Last goal lands between polls after the game left the live feed** — no individual ping, but the
  full-time results post shows the final score. Acceptable at the accepted poll-interval latency.
- **VAR-disallowed goal** — running score drops; cursor resyncs down; nothing posted; a later
  re-scored goal is caught normally.
- **Group send fails** — log + admin DM (existing best-effort pattern); the bot keeps running.
- **Penalty shootout** — excluded from goal posts (not part of the running score); the knockout
  winner still comes from settlement.

## Testing

- **Provider** (`test_api_football.py`): `get_live_results` exposes `live_*` from `goals`;
  `get_goal_events` returns the uncapped timeline (an ET goal included, `Missed Penalty` and shootout
  excluded, `extra` populated, own goal flagged).
- **FakeProvider** (`test_fake_provider.py`): scripted `get_goal_events` + `call_log` assertion.
- **Domain** (`test_live.py`, new): `goal_progression` — own-goal flip, ET minute labels, multi-goal
  sequences, empty timeline.
- **Messages** (`test_text_pt.py`): `kickoff_text`; `goal_text` variants (penalty, own goal,
  missing scorer, stoppage `90+3′`, HTML escaping).
- **Poll job** (`test_poll_job.py`): kickoff posted once on `SCHEDULED→LIVE`; not posted on restart
  (`started_at` set) nor when first seen `FINISHED`; score-gate triggers exactly one events call on
  increase and **none** when unchanged; VAR decrease resyncs without posting; multiple new goals
  posted in order; events call is budgeted (cap path).
- **Migration** (`test_migrations.py`): upgrade/downgrade round-trip; new columns present with
  defaults.
- **Models** (`test_models.py`): defaults (`started_at = None`, `goals_announced = 0`).

## Spec / docs maintenance (§11)

- Add **COMPLETION.md §9.4** documenting the kickoff + goal notifications and the score-gated fetch;
  note the §7.1 `MatchResult.live_*` / `GoalEvent.extra` additions and the new `get_goal_events`
  provider method.
- No command, bet-category, scoring, or grading change → **`/ajuda` is unaffected** (the §11
  maintenance trigger does not fire). README/`.env`/`config.yaml` unchanged (no new config).

## Gates

`ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest` all green; domain coverage
stays at 100% line+branch for `scoring.py` + `settlement.py` (this feature adds no branches there).
Both migrations continue to apply (`alembic upgrade head`).
