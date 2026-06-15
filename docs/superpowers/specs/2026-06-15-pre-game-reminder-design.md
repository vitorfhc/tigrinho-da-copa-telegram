# Design — Pre-game betting reminder (~1h before kickoff)

**Date:** 2026-06-15
**Status:** Approved (revised after multi-agent bug review)
**Spec home:** extends `COMPLETION.md` §9 (new §9.3) and §12.

## Goal

Post a reminder to the group **~1 hour before each game** so people remember to place their
bets. When several games share the **same kickoff time** (real World Cup concurrent matches —
final group-stage games kick off simultaneously by design), send **one combined message** for that
slot rather than one message per game.

Clarified semantics (chosen with the user):

- **Per-game / per-slot.** Each distinct kickoff time gets its own reminder ~1h before it. A game
  kicking off hours after another gets its own reminder.
- **Combine = identical kickoff instant only.** Two games at 16:00 → one message ~15:00. Two games
  at 16:00 and 16:15 (staggered) → two separate reminders, each ~1h before its own kickoff.

## Approach

A **DB-driven repeating reminder sweep** (PTB `JobQueue.run_repeating`), structurally modeled on the
existing group-announcement flow in `sync_job._announce_new_games`, but selecting the **soonest due
kickoff slot** instead of the whole lead window.

Each sweep:
1. Find the **anchor**: the soonest game that is *announced*, *unreminded*, `SCHEDULED`, and inside
   its lead window — `announced_at IS NOT NULL AND reminded_at IS NULL AND status == SCHEDULED AND
   now < kickoff_utc <= now + lead`, ordered by `kickoff_utc`.
2. If there is no anchor → return without posting.
3. Otherwise select **every game sharing the anchor's exact `kickoff_utc`** (same filters). This is
   the slot batch — one game, or several simultaneous games.
4. Build **one** combined pt-BR HTML message + one `🎯 Apostar` deep-link button per game in the
   batch, post to `group_chat_id`.
5. On success → `mark_reminded` (re-validating: only flag games still `SCHEDULED` + unreminded). On
   failure → log + DM admin, leave unflagged so the next sweep retries (until kickoff passes, after
   which `now < kickoff_utc` excludes it).

At most one slot is reminded per sweep. If two different slots are both due in the same sweep (only
possible after downtime), the soonest fires now and the next fires on a following sweep — they are
never merged across kickoff times.

### Why "soonest due slot", not "whole lead window"

The multi-agent review proved a plain `now < kickoff_utc <= now + lead` window **cannot** deliver
slot-combining and mis-combines on restart:

- A game is reminded-and-flagged on the *first* sweep it enters its 60-min window. A 16:00 game is
  flagged ~15:00; a 16:15 game only enters at 15:15, by which time 16:00 is already flagged — so
  even "close" games never share a message; combining was accidental and sweep-phase-dependent.
- After downtime, the first sweep would flush **every** game in the next 60 min into one message,
  some up to ~50 min early.

Selecting only the soonest due kickoff instant makes combining **exactly** "same kickoff time",
deterministically, in steady state and after a restart alike.

### Why this overall shape

- **Restart-safe.** State lives in a DB column (`reminded_at`), exactly like `announced_at`. PTB
  `JobQueue` jobs are in-memory, so a `run_once`-per-game design would lose pending reminders on
  restart.
- **Zero provider/API calls** — pure DB read + group post; independent of the API budget (§7.3).
- **Mirrors existing, tested code** — `list_unannounced`/`mark_announced` →
  `list_due_for_reminder`/`mark_reminded`; `_announce_new_games` → `reminder_job`.
- **Idempotent** — the `reminded_at` gate prevents double-posting across sweeps and restarts.

### Alternatives rejected

- **Per-game `run_once` jobs at `kickoff − lead`.** In-memory jobs are lost on restart; combining
  needs a shared bucket. More moving parts, worse failure mode.
- **Fold into `poll_job`.** That job is budget-sensitive and returns early when no game is *active*;
  reminders fire when games are *upcoming*, so the early-return logic conflicts.
- **Cluster window (combine games within ~N min).** Considered to honor a literal 16:00+16:15 merge;
  rejected because real WC concurrent matches share the exact kickoff time, so same-slot combining is
  the real-world-correct behavior and needs no extra cluster-gap knob.

## Components

1. **Alembic migration** (append-only) — add `games.reminded_at` as a nullable `sa.DateTime()`
   column (no `server_default`; matches `announced_at`/`settled_at`). `down_revision` = the current
   head `b0be15a80128` (`drop_squad_players_team_based_first_…`); `downgrade` drops the column. Use
   the same `op.add_column` / `op.drop_column` style (with `render_as_batch` if the existing
   migrations do) as the prior revisions.
2. **`Game` model** (`db/models.py`) — `reminded_at: Mapped[datetime | None] = mapped_column(
   DateTime, default=None)`, next to `announced_at`.
3. **`GameRepository`** (`db/repositories.py`):
   - `list_due_for_reminder(now: datetime, lead: timedelta) -> list[Game]` — the soonest-due-slot
     selection above (announced, unreminded, `SCHEDULED`, in lead window; then narrowed to the
     minimum `kickoff_utc` among them). Returns `[]` when nothing is due.
   - `mark_reminded(fixture_ids: list[int], when: datetime) -> None` — sets `reminded_at = when`
     **only** for games still `status == SCHEDULED AND reminded_at IS NULL` (re-validate, so a
     game voided/rescheduled mid-flight is not falsely flagged). Twin of `mark_announced` but
     guarded.
4. **`sync_fixtures` reschedule branch** (`bot/sync_job.py`) — when an existing game's kickoff
   changes, also reset `existing.reminded_at = None` (alongside the existing `status` reset) so a
   moved game re-enters the reminder pipeline and is reminded ~1h before its *new* kickoff.
5. **`text_pt.reminder_text(games)`** (`domain/text_pt.py`) — pure pt-BR HTML builder. `games` is a
   sequence of `(home, away, kickoff_local)`, same item shape as `announcement_text`. Example
   (two simultaneous matches; weekday is computed by `format_kickoff_local`, 2026-06-13 is a
   Saturday → `Sáb`):

   ```
   ⏰ Falta ~1h pro apito! Ainda dá pra palpitar:
   • Brasil x Argentina — Sáb 13/06 16:00
   • França x Alemanha — Sáb 13/06 16:00

   Toque em "🎯 Apostar" abaixo para palpitar no privado (fecha no apito inicial).
   ```

   Buttons via `announcement_keyboard([(v.fixture_id, f"{v.home} x {v.away}") …], settings.bot_username)`
   (same call shape as `_announce_new_games`).
6. **`bot/reminder_job.py`** — new module:
   - `reminder_job(context)` callback: wrapped so one bad cycle never kills the bot (§14); on
     `TelegramError` from the post, log + `notify_admin`, leave unflagged. Two-session pattern: read
     `_GameView`s in session A, post, then `mark_reminded` in session B (re-validated as above).
   - `schedule_reminder_job(job_queue, settings)`: `run_repeating(reminder_job,
     interval=settings.reminder_interval_minutes * 60, first=20, name=REMINDER_JOB_NAME)`
     (`first=20` is a small startup offset, staggered from the poll job's `first=10`).
   - Registered in `bot/app.py::post_init` alongside `schedule_sync_job` / `schedule_poll_job`,
     inside the existing `job_queue is not None` guard.
   - `_GameView` is private to `sync_job.py`; `reminder_job.py` defines its own equivalent
     lightweight view (do not import the private one) to keep modules decoupled.
7. **Config** (`config.py` + `config.example.yaml`):
   - `reminder_lead_minutes: int = Field(default=60, gt=0)` — how far before kickoff to remind.
   - `reminder_interval_minutes: int = Field(default=10, gt=0)` — sweep cadence (bounds timing
     accuracy to one interval: with lead 60 / interval 10, a reminder fires ~50–60 min before
     kickoff).
   - Property `reminder_lead -> timedelta` returning `timedelta(minutes=reminder_lead_minutes)`.

## Data flow

```
reminder_job (every reminder_interval_minutes)
  └─ session A: list_due_for_reminder(now, lead)
       │            → [] when no announced+unreminded SCHEDULED game is in its lead window
       │            → else the games sharing the soonest due kickoff instant
       ├─ empty → return (no post)
       └─ reminder_text([(v.home, v.away, v.kickoff_local) …])
          + announcement_keyboard([(v.fixture_id, f"{v.home} x {v.away}") …], settings.bot_username)
            └─ bot.send_message(group_chat_id, HTML, reply_markup, link preview disabled)
                 ├─ ok  → session B: mark_reminded(ids, now)  [only still-SCHEDULED+unreminded]; commit
                 └─ err → log + notify_admin; leave unflagged (retry next sweep)
```

## Edge cases

- **Same kickoff slot** — combined into one message, N buttons (the core requirement). ✓
- **Staggered kickoffs (e.g. 16:00 vs 16:15)** — separate reminders, each ~1h before its own
  kickoff (the soonest-slot query guarantees this). ✓
- **VOID / postponed / cancelled** — excluded by `status == SCHEDULED`.
- **Reschedule before reminding** — not yet selected; reminds when it re-enters the window.
- **Reschedule after reminding** — `reminded_at` is cleared in `sync_fixtures` (component 4), so the
  moved game is reminded again ~1h before its new kickoff. (Fixes review finding #7.)
- **Mid-flight reschedule/void between read and mark** — `mark_reminded` re-validates and skips it.
  (Fixes review finding #6.)
- **Announced-gate** — a game is only reminded after it has been successfully announced
  (`announced_at IS NOT NULL`), so a reminder never precedes/replaces the "novos jogos" post, and a
  game added <1h before kickoff that hasn't yet announced won't be reminded out of order. (Fixes
  review finding #10.)
- **Bot down across the window** — on restart, the soonest due slot reminds (possibly a little late
  but before kickoff); games whose kickoff already passed are skipped (betting closed); multiple
  due slots fire one-per-sweep, never merged. (Resolves review finding #9.)
- **Persistent send failure for the soonest slot** — retried each sweep; it can delay a later slot's
  reminder until the failing slot's kickoff passes. Accepted: send failures already DM the admin,
  and the later slot is still reminded before its own kickoff. Documented, not guarded.
- **Idempotency** — the `reminded_at` gate guarantees no double-post across sweeps or restarts.

## Testing

All four gates green before commit (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`).

- `tests/test_reminder_job.py` — anchor selection; **same-slot combine → one `send_message` with N
  buttons**; **staggered kickoffs across sweeps → two sends** (pins the chosen semantics); only the
  soonest slot fires per sweep; unannounced game not reminded; empty → no post; success →
  `mark_reminded`; send failure → unflagged + admin alerted; one-bad-cycle isolation.
- `tests/test_repositories.py` — `list_due_for_reminder` (in/out of window, same-slot grouping,
  unannounced excluded, voided excluded, kickoff-passed excluded, soonest-slot narrowing) and
  `mark_reminded` (re-validation skips non-SCHEDULED).
- `tests/test_sync_job.py` — reschedule clears `reminded_at`.
- `tests/test_text_pt.py` — `reminder_text` (escaping, multi-game list, **explicit weekday label**
  to lock the `format_kickoff_local` mapping).
- `tests/test_config.py` — defaults + `> 0` validation for the two new settings; `reminder_lead`.
- `tests/test_migrations.py` — upgrade/downgrade round-trip including the new column.
- `tests/test_app.py` — `post_init` schedules the reminder job.

`reminder_text` is a pure builder (no I/O), consistent with the rest of `text_pt.py`.

## Docs

- **`COMPLETION.md`** — add **§9.3 Pre-game reminders** (soonest-due-slot sweep, same-slot combine,
  no provider calls, budget-independent). §12 stays consistent (group posts, no per-user state).
- **`PROGRESS.md`** — record the feature under post-build enhancements.
- **`/ajuda`** — intentionally **not** changed (user decision); no command/category/scoring/grading
  change, so the §11 maintenance rule does not require it.

## Out of scope

- Combining games at *different* kickoff times (cluster window) — rejected above.
- Per-user "who hasn't bet yet" tracking or DM reminders (would reintroduce the subscription state
  §12 removes).
- Configurable message templates or multiple reminders per game.
