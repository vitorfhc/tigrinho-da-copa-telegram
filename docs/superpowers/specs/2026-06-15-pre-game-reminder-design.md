# Design — Pre-game betting reminder (~1h before kickoff)

**Date:** 2026-06-15
**Status:** Approved (pending spec review)
**Spec home:** extends `COMPLETION.md` §9 (new §9.3) and §12.

## Goal

Post a reminder to the group **~1 hour before each game** so people remember to place their
bets. When several games are due within the same sweep, send **one combined message** listing
all of them — never one message per game in that case.

Derived requirement (clarified with the user): reminders are **per-game** — a game kicking off
hours after another gets its own ~1h-before reminder. "Combine" applies only to games whose
reminders coincide (close kickoffs caught in the same sweep).

## Approach

A **DB-driven repeating reminder sweep** (PTB `JobQueue.run_repeating`), structurally identical to
the existing group-announcement flow in `sync_job._announce_new_games`.

Each sweep:
1. Query games **due for reminder**: `status == SCHEDULED AND now < kickoff_utc <= now + lead AND
   reminded_at IS NULL`, soonest first.
2. If none → return without posting.
3. Else build **one** combined pt-BR HTML message + one `🎯 Apostar` deep-link button per game,
   post to `group_chat_id`.
4. On success → `mark_reminded(fixture_ids, now)`. On failure → log + DM admin, leave unmarked so
   the next sweep retries (until kickoff passes, after which the query no longer selects it).

### Why this approach

- **Restart-safe.** State lives in a DB column (`reminded_at`), exactly like `announced_at`. PTB
  `JobQueue` jobs are in-memory, so a `run_once`-per-game design would lose all pending reminders
  on restart.
- **Combines concurrent games for free** — a single query returns the batch.
- **Zero provider/API calls** — pure DB read + group post; independent of the API budget (§7.3).
- **Mirrors existing, tested code** — `list_unannounced`/`mark_announced` →
  `list_due_for_reminder`/`mark_reminded`; `_announce_new_games` → `reminder_job`.
- **Idempotent** — the `reminded_at` gate prevents double-posting, like announcements/settlement.

### Alternatives rejected

- **Per-game `run_once` jobs at `kickoff − lead`.** In-memory jobs are lost on restart (would need
  re-scheduling from the DB on startup — rebuilding this query anyway), and combining concurrent
  games needs a shared bucket. More moving parts, worse failure mode.
- **Fold into `poll_job`.** That job is budget-sensitive and returns early when no game is *active*;
  reminders fire when games are *upcoming*, so the early-return logic conflicts. Wrong coupling.

## Components

1. **Alembic migration** (append-only) — add `games.reminded_at TIMESTAMP NULL` (nullable, default
   `NULL`), mirroring the existing `announced_at` column. Down-revision = current head.
2. **`Game` model** (`db/models.py`) — `reminded_at: Mapped[datetime | None] = mapped_column(
   DateTime, default=None)`, placed next to `announced_at`.
3. **`GameRepository`** (`db/repositories.py`):
   - `list_due_for_reminder(now: datetime, lead: timedelta) -> list[Game]` — the due-window query
     above, ordered by `kickoff_utc`.
   - `mark_reminded(fixture_ids: list[int], when: datetime) -> None` — twin of `mark_announced`.
4. **`text_pt.reminder_text(games)`** (`domain/text_pt.py`) — pure pt-BR HTML builder. `games` is a
   sequence of `(home, away, kickoff_local)`, same item shape as `announcement_text`. Example:

   ```
   ⏰ Falta ~1h pro apito! Ainda dá pra palpitar:
   • Brasil x Argentina — Sáb 16/06 16:00
   • França x Alemanha — Sáb 16/06 16:15

   Toque em "🎯 Apostar" abaixo para palpitar no privado (fecha no apito inicial).
   ```

   Reuses `announcement_keyboard` for the buttons (one per game).
5. **`bot/reminder_job.py`** — new module:
   - `reminder_job(context)` callback: wrapped so one bad cycle never kills the bot (§14); on
     `TelegramError` from the post, log + `notify_admin`, leave unmarked.
   - `schedule_reminder_job(job_queue, settings)`: `run_repeating(reminder_job,
     interval=settings.reminder_interval_minutes * 60, first=20, name=REMINDER_JOB_NAME)`.
     (`first=20` is a small startup offset, staggered from the poll job's `first=10`.)
   - Registered in `bot/app.py::post_init` alongside `schedule_sync_job` / `schedule_poll_job`.
   - Follows `_announce_new_games`'s two-session pattern (read views in one session, mark in a
     second session after a successful send) to keep ORM objects off the network path.
6. **Config** (`config.py` + `config.example.yaml`):
   - `reminder_lead_minutes: int = Field(default=60, gt=0)` — how far before kickoff to remind.
   - `reminder_interval_minutes: int = Field(default=10, gt=0)` — sweep cadence (bounds timing
     accuracy to one interval: with lead 60 / interval 10, a reminder fires 50–60 min before
     kickoff).
   - Convenience property `reminder_lead` returning `timedelta(minutes=reminder_lead_minutes)`.

## Data flow

```
reminder_job (every reminder_interval_minutes)
  └─ session A: list_due_for_reminder(now, lead) → [_GameView…]
       ├─ empty → return (no post)
       └─ reminder_text([(v.home, v.away, v.kickoff_local) …]) + announcement_keyboard([(v.fixture_id, label) …])
            └─ bot.send_message(group_chat_id, HTML, reply_markup, no link preview)
                 ├─ ok  → session B: mark_reminded(ids, now); commit
                 └─ err → log + notify_admin; leave unmarked (retry next sweep)
```

## Edge cases

- **VOID / postponed / cancelled** — excluded by `status == SCHEDULED`.
- **Rescheduled before reminding** — if a reschedule moves kickoff out of the window, the game is
  simply not selected yet (`reminded_at` still `NULL`); it reminds when it re-enters the window.
- **Rescheduled after reminding** — not re-reminded (`reminded_at` set). Acceptable: the reschedule
  already posts its own group notice (`reannounce_text`).
- **Bot down across the window** — on restart, games with `now < kickoff` and `reminded_at IS NULL`
  remind late but before kickoff; games whose kickoff already passed are skipped (betting closed).
- **Multiple games same kickoff slot** — one combined message, N buttons (the core requirement).
- **Idempotency** — the `reminded_at` gate guarantees no double-post across sweeps or restarts.

## Testing

All four gates green before commit (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`).

- `tests/test_reminder_job.py` — due-window selection (inside/outside window, already-reminded,
  VOID, kickoff-already-passed), combine-multiple → one `send_message` with N buttons, empty → no
  post, success → `mark_reminded`, send failure → unmarked + admin alerted, one-bad-cycle isolation.
- `tests/test_repositories.py` — `list_due_for_reminder` / `mark_reminded`.
- `tests/test_text_pt.py` — `reminder_text` (escaping, multi-game list, button labels).
- `tests/test_config.py` — defaults + `> 0` validation for the two new settings.
- `tests/test_migrations.py` — upgrade/downgrade round-trip including the new column.
- `tests/test_app.py` — `post_init` schedules the reminder job.

`reminder_text` is a pure builder (no I/O), so it carries no special coverage burden beyond the
domain-purity rule already applied to `text_pt.py`.

## Docs

- **`COMPLETION.md`** — add **§9.3 Pre-game reminders** describing this job; note it makes no
  provider calls and is independent of the budget. §12 stays consistent (reminders are group posts,
  no per-user subscription state).
- **`PROGRESS.md`** — record the feature under post-build enhancements.
- **`/ajuda`** — intentionally **not** changed (user decision); no command/category/scoring/grading
  change, so the §11 maintenance rule does not require it.

## Out of scope

- Per-user "who hasn't bet yet" tracking or DM reminders (would reintroduce the subscription state
  that §12 deliberately removes).
- Configurable reminder message templates or multiple reminders per game.
