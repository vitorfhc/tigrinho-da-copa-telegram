# Score reconciliation after settlement — design (v2)

**Date:** 2026-06-16
**Status:** revised after multi-POV review (verdict: ready-with-changes); pending final user sign-off
**Spec refs:** COMPLETION.md §7.3 (budget), §8.3 (settlement & results), §9.2 (live polling & auto-settlement)

## Problem

Production posted **France 3 × 0 Senegal** for fixture `1489383` (WC2026 group stage, kickoff
2026-06-16 19:00 UTC) and graded all 29 bets against it. The real result is **France 3 × 1
Senegal** — Senegal scored at 90+5′ (I. Mbaye), France again at 90+6′.

Root cause (confirmed against the live API + the prod DB row + logs):

- The bot settled via the `due` path (`kickoff + SETTLE_AFTER` = 2h) at **21:04:30 UTC**, ~10 min
  after the final whistle.
- At that instant the API feed had ingested France's three goals but **not yet** Senegal's 90+5′
  goal (a late/VAR-delayed feed entry): `score.fulltime` was `3-0`, running `goals` was `3-0`.
  Prod row confirms `home_goals_90=3, away_goals_90=0, goals_announced=3`.
- `settle_fixture` reads `score.fulltime` exactly once and is **idempotent**; once settled, the
  poll job never re-reads (`game.settled_at is not None → return`). The API later corrected itself
  to `3-1`, but the bot had frozen `3-0` and never reconciled.

This is a **late-data / single-read** problem, not a parsing bug.

## Goal

After a game is settled, keep re-checking the provider's result for a bounded window. If the graded
**outcome** the provider implies changes, **re-grade** the bets (idempotent `settle_fixture`) and —
only when it actually moves someone's standing — post a **correction** to the group.

Non-goals: changing how the 90′ score is parsed (§7.2 unchanged); changing scoring/grading rules;
reconciling games outside the window (manual CLI `set-result` remains the fallback); backfilling
missed live goal posts.

## Approach

A dedicated **reconcile job** (`JobQueue.run_repeating`), separate from the 1-min live poll, acting
on **already-settled** games. Unlike the poll job it must run even when no game is "active".

### Per-game schedule (backoff)

Anchored on each game's own timestamps:

- **First check:** ~5 min after `settled_at` (catch quick VAR/late-feed adds).
- **Then:** every 30 min.
- **Until:** `kickoff_utc + reconcile_window_hours` (6h). After that, no automatic reconciliation
  (manual CLI `set-result` remains). Coverage for a game is `max(0, kickoff+window − settled_at)`;
  for the normal 2h settle that is ~4h — ample for VAR/feed corrections that land within an hour.

A new nullable column `games.last_reconciled_at` records the last re-check so the schedule survives
restarts. The job's base tick is the small interval (`reconcile_first_delay_minutes`, 5 min) so the
first check fires at ~5-min granularity; the per-game "due?" test gates the budgeted call:

```
due = (last_reconciled_at is None and now >= settled_at + first_delay)
      or (last_reconciled_at is not None and now >= last_reconciled_at + steady_interval)
```

On a normal completed due-check we set `last_reconciled_at = now`. On a **transient** read
(provider not FINISHED, 90′ score incomplete, or empty-`/fixtures` `ProviderError`) we do **not**
advance it — so the next 5-min base tick retries quickly during the in-flux window instead of
burning the 30-min cooldown.

### Config (config.yaml, validated in `Settings`, all `gt=0`)

- `reconcile_window_hours: int = 6`
- `reconcile_first_delay_minutes: int = 5` — also the job base tick
- `reconcile_interval_minutes: int = 30` — steady cadence after the first check
- `reconcile_budget_reserve: int = 25` — reconcile yields when `budget.remaining() <` this, so it
  never starves real-time settlement/sync (see Budget).

### Budget (§7.3) — reconcile is LOWEST priority

`api_daily_cap` default is **100** (`config.py:74`); **prod sets 7000** (`config.yaml`). Priority is
`settlement > polling > reconcile`. Each reconcile due-check is **one** budget unit but **two**
real API-Football HTTP requests (`get_match_result` → `/fixtures?id=` + `/fixtures/events`,
`api_football.py:285,288`); `budget.guarded` counts it as 1 (`budget.py:88`). Per game: ~1 first +
≤11 steady ≈ **~12 budget units** over a 6h window.

To guarantee reconcile never causes a settlement read to hit `BudgetExceeded`, the job **skips its
entire pass when `budget.remaining() < reconcile_budget_reserve`** (logged once/day), reserving
headroom for sync (1) + live polling + settlement reads. Worst-case day (~4 concurrent in-window
games × ~12) ≈ ~48 units — comfortable under prod's 7000 and, with the reserve guard, safe even
against the 100 default.

> **Decision (review item #2):** we deliberately fetch the *full* `get_match_result` (with events)
> on every due-check rather than a cheap score-only read, because the broadened change gate
> (below) must detect first-scorer / advancing-team corrections, which need the goal timeline. The
> reserve guard + prod cap make the cost acceptable. A future split-read optimization (score-only
> probe, full fetch on change) is possible but is not worth the extra provider surface here.

### Reconcile pass (one job tick)

1. `now = utcnow()`. If `budget.remaining() < reconcile_budget_reserve` → log once/day, return.
2. `games = GameRepository(session).list_reconcilable(now, reconcile_window_hours)` — `FINISHED`,
   `settled_at IS NOT NULL`, `kickoff_utc >= now − window`.
3. Filter to those **due** per the schedule.
4. For each due game, budget-guarded `get_match_result(fixture_id)`, then in a fresh session:
   - **Re-assert at write time:** re-read the game row; if it is no longer `FINISHED` /
     `settled_at` is NULL → skip (it was voided/rescheduled). If the fresh provider `result.status`
     is not `FINISHED` or its 90′ goals are incomplete → transient: do **not** advance
     `last_reconciled_at`; skip.
   - **Compute the would-be graded outcome in-memory** with the pure `settle_game` (no writes):
     per-bet `(is_correct, points)`, plus the would-be first-scorer team and `advancing_team_id`
     and 90′ score. Compare against what is stored (`bet.points_awarded` per bet,
     `game.first_scorer_player_id`, `game.advancing_team_id`, `game.home/away_goals_90`).
   - **Unchanged** (all identical) → set `last_reconciled_at = now`; post nothing.
   - **Changed** → snapshot `prev_score = (game.home_goals_90, game.away_goals_90)` and each
     player's **old total** *before* re-grading; call `settle_fixture(session, game, result)`
     (idempotent re-grade; rewrites score, advancing, first scorer, every bet grade); set
     `last_reconciled_at = now`; `session.commit()`.
     - Compute new per-player totals from the returned summary. **Post only if at least one
       player's total changed**, and only if this game's group-correction count `< correction post
       cap (2)`. Otherwise re-grade silently (a score change that moves no standing, or a 3rd+
       oscillation, posts nothing; the cap overflow DMs the admin once).
     - The correction post mentions **only affected players** (others rendered as plain escaped
       names) to avoid pinging 29 people on every correction.
5. Best-effort group send (§14): on `TelegramError`, log + DM admin; the re-grade is committed.

**Error handling** mirrors `poll_job`: wrap the tick — `BudgetExceeded` → `alert_cap_reached`; any
other `Exception` → log + DM admin; never crash the bot.

### Concurrency (review item #7)

`budget.py:8` records the project assumption of a **single event loop with non-overlapping
scheduled jobs**, and the 5-min first-check delay means reconcile never acts on a game in the same
instant the poll job settles it (poll won't touch an already-settled game; reconcile won't touch a
game until ≥5 min after `settled_at`). So a heavyweight cross-job lock is unnecessary. The
**write-time re-assert** in step 4 (re-read `FINISHED`/`settled_at` and re-check fresh provider
status before `settle_fixture`) is the needed guard; it also prevents re-grading a game voided
after settlement. (If we later allow overlapping jobs, add a shared `asyncio.Lock` in `AppContext`
around the settle+send critical section in both jobs.)

### Correction message (review item #5)

A dedicated `correction_text(...)` in `domain/text_pt.py` (keeping `results_text` untouched):

```
⚠️ <b>Placar corrigido!</b>
Os pontos deste jogo foram recalculados — confira o /placar.
🏁 <b>France 3 x 1 Senegal</b> (antes: 3 x 0)
⚽ Primeira equipe a marcar: France

<b>Pontuação recalculada:</b>
<affected players, each: mention — old → new pts; unaffected omitted or shown plain>
```

Variants:
- **Score up** — as above.
- **Score down** (goal disallowed, e.g. 3-1 → 3-0) — same shape; lead notes "gol anulado" so
  players who *lose* points understand why.
- **Score unchanged but outcome changed** (first-scorer / advancing reclassified) — omit the
  `(antes: …)` score suffix; lead with "Primeira equipe a marcar corrigida" / "Classificado
  corrigido" instead.

`corrected_from = prev_score` is passed only when the score actually changed (snapshotted before
`settle_fixture` mutates the row).

## Components touched

| File | Change |
|------|--------|
| `tigrinho/config.py` | + `reconcile_window_hours`, `reconcile_first_delay_minutes`, `reconcile_interval_minutes`, `reconcile_budget_reserve` |
| `config.example.yaml` | document the four new settings |
| `tigrinho/db/models.py` | + `Game.last_reconciled_at: Mapped[datetime \| None]` |
| `tigrinho/db/migrations/versions/<new>.py` | append-only migration; `down_revision = 'd2e3f4a5b6c7'` (verify `alembic heads`); `op.batch_alter_table('games').add_column(... nullable=True)` |
| `tigrinho/db/repositories.py` | + `GameRepository.list_reconcilable(now, window_hours)` |
| `tigrinho/bot/reconcile_job.py` (new) | `reconcile_job` + `schedule_reconcile_job` (`run_repeating`, `first≈75s`, interval `= reconcile_first_delay_minutes*60`) |
| `tigrinho/bot/app.py` | wire `schedule_reconcile_job` |
| `tigrinho/bot/runtime.py` | `AppContext`: in-memory `reconcile_posts: dict[int,int]` (per-game post cap) + `reconcile_alerted` dedup set (transient-error / reserve DMs) |
| `tigrinho/domain/text_pt.py` | + `correction_text(...)` (affected-only mentions, per-player delta, score/first-scorer/advancing variants) |
| `COMPLETION.md` | document reconciliation in §8.3/§9.2 + the four config keys in §4/§19 |
| `PROGRESS.md` | note the change |

`/ajuda` is **not** touched: no new command; bet categories / scoring / grading rules unchanged
(maintenance rule §11 satisfied by the COMPLETION.md update).

**Accepted side effect (review item: `list_recently_ended` reorder):** a reconcile re-settle
rewrites `settled_at=now`, so a corrected game jumps to the top of the `/placar_jogo[s]`
recently-ended picker (ordered by `settled_at.desc()`). This is acceptable — the game genuinely was
just re-settled — and is documented rather than worked around.

## Testing (TDD)

- **Repository** (`test_repositories.py`): `list_reconcilable` includes a settled in-window game;
  excludes unsettled, settled-past-window, and non-FINISHED games.
- **Reconcile job** (`test_reconcile_job.py`, new) with a fake provider + fake bot:
  - score changed (`3-0` → `3-1`), due → re-grades; row becomes `3-1`; correction sent mentioning
    only affected players.
  - score change that moves no player total → re-grades silently, no post.
  - first-scorer-only change (same score) → re-grades + posts the first-scorer variant.
  - not yet due (`< settled_at + 5min`) → no provider call.
  - past window → not selected.
  - provider non-FINISHED / incomplete → no re-grade, `last_reconciled_at` **not** advanced.
  - game voided after settlement → write-time re-assert skips it.
  - correction post cap reached → silent re-grade + single admin DM.
  - `budget.remaining() < reserve` → pass yields, no provider calls.
- **Text** (`test_text_pt.py`): `correction_text` renders score-up, score-down ("gol anulado"),
  and first-scorer-only variants; affected-only mentions; per-player `antes → agora`.

Domain purity unaffected (`domain/scoring.py` / `domain/settlement.py` untouched).

## One-off correction of fixture 1489383 (timing)

At spec time current UTC ≈ 21:35; the 6h window closes **2026-06-17 01:00 UTC**. Two paths:

- **(Preferred) Ship the feature before 01:00 UTC** → the running reconcile job auto-detects `3-0`
  vs API `3-1`, re-grades the 29 bets, posts the correction. No manual data edit.
- **Fallback if the window closes first** → CLI in the prod container:
  `docker compose exec -T bot python -m tigrinho.cli set-result 1489383 3 1 --first-team home`
  (`--first-team home` preserves FIRST_TEAM; synthetic timeline; **does not** post to the group).

Given the expanded scope from the review, finishing all of the above with green gates before 01:00
is tight. **Open decision for the user:** (a) implement the full reviewed feature at quality and use
the CLI fallback + a one-off group post for 1489383, or (b) prioritize shipping enough to auto-fix
before 01:00. See the message accompanying this spec.

## Rollout

1. Implement + green gates (`ruff`, `ruff format --check`, `mypy --strict`, `pytest`).
2. Commit on the worktree branch; merge to `main`; push.
3. `scp` the updated `config.yaml` to `bbdo` (adds the four new keys; defaults are safe if omitted).
4. `ssh bbdo … git pull --ff-only && docker compose up -d --build` (migration runs on container
   start via the entrypoint).
5. Verify: container `Up`, migration applied; the group receives the France 3 × 1 Senegal
   correction; DB row reads `away_goals_90=1`.
