# Pre-game Betting Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post one group reminder ~1h before each kickoff so people remember to bet, combining games that share the same kickoff time.

**Architecture:** A new `JobQueue.run_repeating` job (`bot/reminder_job.py`) sweeps every `reminder_interval_minutes`, selects the **soonest due kickoff slot** (announced, unreminded, `SCHEDULED`, `now < kickoff_utc <= now + lead`) via a new repository method, posts one combined pt-BR message with a `🎯 Apostar` deep-link button per game, and marks those games reminded only on a successful send. Idempotency lives in a new nullable `games.reminded_at` column (append-only Alembic migration), exactly like `announced_at`. No provider/API calls.

**Tech Stack:** python-telegram-bot 22.x (`JobQueue`), SQLAlchemy 2.0 + Alembic, pydantic-settings, structlog, pytest + pytest-asyncio. Gates: `ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest`.

**Design spec:** `docs/superpowers/specs/2026-06-15-pre-game-reminder-design.md`

---

## File Structure

- **Modify** `tigrinho/config.py` — add `reminder_lead_minutes`, `reminder_interval_minutes`, `reminder_lead` property.
- **Modify** `config.example.yaml` — document the two new settings.
- **Modify** `tigrinho/db/models.py` — add `Game.reminded_at`.
- **Create** `tigrinho/db/migrations/versions/7f3a9c2b1e04_add_game_reminded_at.py` — add the column.
- **Modify** `tigrinho/db/repositories.py` — `GameRepository.list_due_for_reminder` + `mark_reminded`.
- **Modify** `tigrinho/bot/sync_job.py` — clear `reminded_at` in the reschedule branch.
- **Modify** `tigrinho/domain/text_pt.py` — `reminder_text` pure builder.
- **Create** `tigrinho/bot/reminder_job.py` — the job + scheduler.
- **Modify** `tigrinho/bot/app.py` — schedule the job in `post_init`.
- **Create** `tests/test_reminder_job.py`; **modify** `tests/test_config.py`, `tests/test_repositories.py`, `tests/test_sync_job.py`, `tests/test_text_pt.py`, `tests/test_app.py`.
- **Modify** `COMPLETION.md` (§9.3), `PROGRESS.md`.

---

## Task 1: Config settings (`reminder_lead_minutes`, `reminder_interval_minutes`)

**Files:**
- Modify: `tigrinho/config.py`
- Modify: `config.example.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add the two new field-env names to the cleanup list and write failing tests**

In `tests/test_config.py`, add to `_FIELD_ENV_NAMES` (after `"POLL_INTERVAL_MINUTES",`):

```python
    "REMINDER_LEAD_MINUTES",
    "REMINDER_INTERVAL_MINUTES",
```

Add this import at the top (next to `from datetime import time`):

```python
from datetime import time, timedelta
```

Add these tests at the end of the file:

```python
def test_reminder_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.reminder_lead_minutes == 60
    assert settings.reminder_interval_minutes == 10
    assert settings.reminder_lead == timedelta(minutes=60)


def test_reminder_interval_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, env={"REMINDER_INTERVAL_MINUTES": "0"})


def test_reminder_lead_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, env={"REMINDER_LEAD_MINUTES": "0"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::test_reminder_defaults -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'reminder_lead_minutes'`.

- [ ] **Step 3: Implement the settings**

In `tigrinho/config.py`, change the datetime import:

```python
from datetime import time, timedelta
```

Add the two fields right after the `poll_interval_minutes` line:

```python
    poll_interval_minutes: int = Field(default=10, gt=0)
    reminder_lead_minutes: int = Field(default=60, gt=0)
    reminder_interval_minutes: int = Field(default=10, gt=0)
```

Add this property right after the `sync_time_obj` property:

```python
    @property
    def reminder_lead(self) -> timedelta:
        """How far before kickoff to post the betting reminder (§9.3)."""
        return timedelta(minutes=self.reminder_lead_minutes)
```

- [ ] **Step 4: Document the settings in `config.example.yaml`**

Add after the `poll_interval_minutes` line:

```yaml
reminder_lead_minutes: 60              # post a betting reminder this long before kickoff
reminder_interval_minutes: 10          # how often the reminder sweep runs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (all, including the three new tests).

- [ ] **Step 6: Commit**

```bash
git add tigrinho/config.py config.example.yaml tests/test_config.py
git commit -m "feat: reminder_lead_minutes + reminder_interval_minutes config"
```

---

## Task 2: `Game.reminded_at` column + Alembic migration

**Files:**
- Modify: `tigrinho/db/models.py:86` (next to `announced_at`)
- Create: `tigrinho/db/migrations/versions/7f3a9c2b1e04_add_game_reminded_at.py`
- Test: `tests/test_migrations.py` (existing `test_upgrade_head_matches_orm_metadata` is the gate)

- [ ] **Step 1: Add the column to the ORM model (this makes the migration test fail)**

In `tigrinho/db/models.py`, add `reminded_at` right after the `announced_at` line:

```python
    announced_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
```

- [ ] **Step 2: Run the migration test to verify it fails**

Run: `pytest tests/test_migrations.py::test_upgrade_head_matches_orm_metadata -v`
Expected: FAIL — the migrated `games` table is missing `reminded_at` that the ORM now declares.

- [ ] **Step 3: Write the migration**

Create `tigrinho/db/migrations/versions/7f3a9c2b1e04_add_game_reminded_at.py`:

```python
"""add games.reminded_at (pre-game reminders)

Revision ID: 7f3a9c2b1e04
Revises: b0be15a80128
Create Date: 2026-06-15 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f3a9c2b1e04"
down_revision: str | None = "b0be15a80128"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reminded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.drop_column("reminded_at")
```

- [ ] **Step 4: Run the migration tests to verify they pass**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS (upgrade matches ORM; downgrade-to-base still drops model tables).

- [ ] **Step 5: Commit**

```bash
git add tigrinho/db/models.py tigrinho/db/migrations/versions/7f3a9c2b1e04_add_game_reminded_at.py
git commit -m "feat: add games.reminded_at column + migration"
```

---

## Task 3: Repository methods `list_due_for_reminder` + `mark_reminded`

**Files:**
- Modify: `tigrinho/db/repositories.py` (in `GameRepository`, after `mark_announced`)
- Test: `tests/test_repositories.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repositories.py`. First confirm the existing imports include `datetime`/`timedelta` and `Game`/`GameRepository`; if `timedelta` is not imported, add it. Then add:

```python
def test_list_due_for_reminder_picks_soonest_slot(session: Session) -> None:
    from datetime import datetime, timedelta

    from tigrinho.db.models import Game, GameStatus, Stage

    now = datetime(2026, 6, 13, 18, 0)  # naive UTC
    repo = GameRepository(session)

    def _add(fixture_id: int, minutes: int, *, announced: bool = True) -> None:
        kickoff = now + timedelta(minutes=minutes)
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=1,
                home_team_name="A",
                away_team_id=2,
                away_team_name="B",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
                announced_at=now if announced else None,
            )
        )
    _add(1, 30)   # soonest slot
    _add(2, 30)   # same slot as #1 -> combine
    _add(3, 40)   # later slot -> excluded this sweep
    _add(4, 30, announced=False)  # not announced -> excluded
    session.flush()

    due = repo.list_due_for_reminder(now, timedelta(minutes=60))
    assert {g.fixture_id for g in due} == {1, 2}


def test_list_due_for_reminder_excludes_out_of_window_and_voided(session: Session) -> None:
    from datetime import datetime, timedelta

    from tigrinho.db.models import Game, GameStatus, Stage

    now = datetime(2026, 6, 13, 18, 0)
    repo = GameRepository(session)

    def _add(fixture_id: int, minutes: int, *, status: GameStatus = GameStatus.SCHEDULED) -> None:
        kickoff = now + timedelta(minutes=minutes)
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=1,
                home_team_name="A",
                away_team_id=2,
                away_team_name="B",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=status,
                announced_at=now,
            )
        )
    _add(1, -5)    # already kicked off -> excluded
    _add(2, 90)    # beyond the 60-min lead -> excluded
    _add(3, 20, status=GameStatus.VOID)  # voided -> excluded
    session.flush()

    assert repo.list_due_for_reminder(now, timedelta(minutes=60)) == []


def test_mark_reminded_only_flags_scheduled_unreminded(session: Session) -> None:
    from datetime import datetime, timedelta

    from tigrinho.db.models import Game, GameStatus, Stage

    now = datetime(2026, 6, 13, 18, 0)
    repo = GameRepository(session)
    kickoff = now + timedelta(minutes=30)
    session.add(
        Game(
            fixture_id=1,
            match_hash="h1",
            stage=Stage.GROUP,
            home_team_id=1,
            home_team_name="A",
            away_team_id=2,
            away_team_name="B",
            kickoff_utc=kickoff,
            kickoff_local=kickoff,
            status=GameStatus.VOID,  # not SCHEDULED -> must NOT be flagged
            announced_at=now,
        )
    )
    session.flush()

    repo.mark_reminded([1], now)
    game = repo.get(1)
    assert game is not None
    assert game.reminded_at is None  # re-validation skipped a non-SCHEDULED game
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repositories.py::test_list_due_for_reminder_picks_soonest_slot -v`
Expected: FAIL with `AttributeError: 'GameRepository' object has no attribute 'list_due_for_reminder'`.

- [ ] **Step 3: Implement the repository methods**

In `tigrinho/db/repositories.py`, confirm `timedelta` is imported (it is: `from datetime import date, datetime, timedelta`). Add inside `GameRepository`, right after `mark_announced`:

```python
    def list_due_for_reminder(self, now: datetime, lead: timedelta) -> list[Game]:
        """Games of the soonest unreminded kickoff slot due for a ~1h reminder (§9.3).

        Selects announced, still-open, unreminded games inside their lead window
        (``now < kickoff_utc <= now + lead``), then narrows to those sharing the *soonest*
        such ``kickoff_utc`` — so only one kickoff slot is reminded per sweep and "combine"
        means exactly "same kickoff time".
        """
        stmt = (
            select(Game)
            .where(
                Game.status == GameStatus.SCHEDULED,
                Game.announced_at.is_not(None),
                Game.reminded_at.is_(None),
                Game.kickoff_utc > now,
                Game.kickoff_utc <= now + lead,
            )
            .order_by(Game.kickoff_utc)
        )
        due = list(self._session.execute(stmt).scalars())
        if not due:
            return []
        soonest = due[0].kickoff_utc
        return [game for game in due if game.kickoff_utc == soonest]

    def mark_reminded(self, fixture_ids: list[int], when: datetime) -> None:
        """Flag games reminded — only if still SCHEDULED and not already flagged (§9.3).

        Re-validates so a game voided/rescheduled between the read and this write is not
        falsely marked (it must remain eligible for a later, real reminder).
        """
        for fixture_id in fixture_ids:
            game = self._session.get(Game, fixture_id)
            if game is not None and game.status is GameStatus.SCHEDULED and game.reminded_at is None:
                game.reminded_at = when
        self._session.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repositories.py -v`
Expected: PASS (all, including the three new tests).

- [ ] **Step 5: Commit**

```bash
git add tigrinho/db/repositories.py tests/test_repositories.py
git commit -m "feat: GameRepository.list_due_for_reminder + mark_reminded"
```

---

## Task 4: Clear `reminded_at` on reschedule (`sync_fixtures`)

**Files:**
- Modify: `tigrinho/bot/sync_job.py` (the reschedule `elif` branch, around line 100-110)
- Test: `tests/test_sync_job.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync_job.py`:

```python
def test_sync_reschedule_clears_reminded_at(session: Session) -> None:
    from tigrinho.db.models import utcnow

    sync_fixtures(session, [_fx(1)], tz=_TZ)
    game = GameRepository(session).get(1)
    assert game is not None
    game.reminded_at = utcnow()  # pretend the ~1h reminder already fired
    session.flush()

    sync_fixtures(session, [_fx(1, kickoff=_K2)], tz=_TZ)  # reschedule
    moved = GameRepository(session).get(1)
    assert moved is not None
    assert moved.reminded_at is None  # cleared -> will be reminded again before the new kickoff
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_sync_job.py::test_sync_reschedule_clears_reminded_at -v`
Expected: FAIL — `moved.reminded_at` is still the old timestamp, not `None`.

- [ ] **Step 3: Implement the reset**

In `tigrinho/bot/sync_job.py`, in the reschedule `elif` branch, add the `reminded_at` reset alongside the existing status reset:

```python
            existing.kickoff_utc = kickoff_utc
            existing.kickoff_local = kickoff_local
            existing.match_hash = match_hash(fixture)
            existing.status = GameStatus.SCHEDULED
            existing.reminded_at = None
            outcome.rescheduled_games.append(existing)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_job.py -v`
Expected: PASS (the new test plus all existing sync tests).

- [ ] **Step 5: Commit**

```bash
git add tigrinho/bot/sync_job.py tests/test_sync_job.py
git commit -m "feat: clear reminded_at when a game is rescheduled"
```

---

## Task 5: `reminder_text` message builder

**Files:**
- Modify: `tigrinho/domain/text_pt.py` (add after `announcement_text`)
- Test: `tests/test_text_pt.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_text_pt.py` (it already imports from `tigrinho.domain.text_pt`; add `reminder_text` to that import or import inline):

```python
def test_reminder_text_lists_games_with_weekday() -> None:
    from datetime import datetime

    from tigrinho.domain.text_pt import reminder_text

    text = reminder_text(
        [
            ("Brasil", "Argentina", datetime(2026, 6, 13, 16, 0)),
            ("França", "Alemanha", datetime(2026, 6, 13, 16, 0)),
        ]
    )
    assert "Falta ~1h" in text
    assert "Brasil x Argentina — Sáb 13/06 16:00" in text  # 2026-06-13 is a Saturday
    assert "França x Alemanha — Sáb 13/06 16:00" in text
    assert "🎯 Apostar" in text


def test_reminder_text_escapes_team_names() -> None:
    from datetime import datetime

    from tigrinho.domain.text_pt import reminder_text

    text = reminder_text([("A & B", "C", datetime(2026, 6, 13, 16, 0))])
    assert "A &amp; B" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_text_pt.py::test_reminder_text_lists_games_with_weekday -v`
Expected: FAIL with `ImportError: cannot import name 'reminder_text'`.

- [ ] **Step 3: Implement the builder**

In `tigrinho/domain/text_pt.py`, add right after `announcement_text`:

```python
def reminder_text(games: Sequence[tuple[str, str, datetime]]) -> str:
    """~1h pre-kickoff betting reminder for one kickoff slot (§9.3).

    Each item: ``(home, away, kickoff_local)``. Combined into a single message when several
    games share the slot. Followed by one ``🎯 Apostar`` button per game (built separately).
    """
    lines = [
        f"• {escape(home)} x {escape(away)} — {format_kickoff_local(kickoff)}"
        for home, away, kickoff in games
    ]
    body = "\n".join(lines)
    return (
        "⏰ <b>Falta ~1h pro apito! Ainda dá pra palpitar:</b>\n\n"
        f"{body}\n\n"
        'Toque em "🎯 Apostar" abaixo para palpitar no privado (fecha no apito inicial).'
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_text_pt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tigrinho/domain/text_pt.py tests/test_text_pt.py
git commit -m "feat: reminder_text pt-BR message builder"
```

---

## Task 6: `reminder_job` module (job callback + scheduler)

**Files:**
- Create: `tigrinho/bot/reminder_job.py`
- Test: `tests/test_reminder_job.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reminder_job.py`:

```python
"""Tests for the pre-game reminder job (COMPLETION.md §9.3, §16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session, sessionmaker
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.reminder_job import REMINDER_JOB_NAME, reminder_job, schedule_reminder_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import GameRepository
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed(
    session_factory: sessionmaker[Session],
    *,
    fixture_id: int,
    minutes: int,
    announced: bool = True,
) -> None:
    kickoff = _now() + timedelta(minutes=minutes)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=1,
                home_team_name="Brasil",
                away_team_id=2,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
                announced_at=_now() if announced else None,
            )
        )
        session.commit()


def _ctx(app_context: AppContext) -> tuple[MagicMock, AsyncMock]:
    context = MagicMock()
    context.application.bot_data = {APP_CONTEXT_KEY: app_context}
    bot = AsyncMock()
    context.bot = bot
    return context, bot


def _app_context(settings: Settings, session_factory: sessionmaker[Session]) -> AppContext:
    budget = RequestBudget(
        session_factory, daily_cap=settings.api_daily_cap, reset_tz=settings.budget_tzinfo
    )
    return AppContext(
        settings=settings, provider=FakeProvider(), session_factory=session_factory, budget=budget
    )


def _reminded_at(
    session_factory: sessionmaker[Session], fixture_id: int
) -> datetime | None:
    """Read back a game's reminded_at (asserting it exists) — keeps assertions mypy-clean."""
    with session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        assert game is not None
        return game.reminded_at


async def test_combines_same_slot_into_one_message(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, minutes=30)
    _seed(session_factory, fixture_id=2, minutes=30)  # same slot
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    assert bot.send_message.await_count == 1
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert len(markup.inline_keyboard) == 2  # one 🎯 Apostar button per game
    assert _reminded_at(session_factory, 1) is not None
    assert _reminded_at(session_factory, 2) is not None


async def test_staggered_slots_remind_separately_across_sweeps(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, minutes=30)  # soonest slot
    _seed(session_factory, fixture_id=2, minutes=45)  # later slot
    app_context = _app_context(settings, session_factory)

    context1, bot1 = _ctx(app_context)
    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context1))
    assert bot1.send_message.await_count == 1
    assert _reminded_at(session_factory, 1) is not None
    assert _reminded_at(session_factory, 2) is None  # later slot NOT yet reminded

    context2, bot2 = _ctx(app_context)
    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context2))
    assert bot2.send_message.await_count == 1  # the later slot now reminds
    assert _reminded_at(session_factory, 2) is not None


async def test_unannounced_game_is_not_reminded(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, minutes=30, announced=False)
    context, bot = _ctx(_app_context(settings, session_factory))

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    bot.send_message.assert_not_awaited()


async def test_no_due_games_posts_nothing(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, minutes=120)  # outside the 60-min lead
    context, bot = _ctx(_app_context(settings, session_factory))

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    bot.send_message.assert_not_awaited()


async def test_send_failure_leaves_unflagged_and_alerts_admin(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _seed(session_factory, fixture_id=1, minutes=30)
    app_context = _app_context(settings, session_factory)
    context, bot = _ctx(app_context)
    bot.send_message.side_effect = TelegramError("group unreachable")

    await reminder_job(cast(ContextTypes.DEFAULT_TYPE, context))

    # The reminder send failed, then a best-effort admin DM was attempted (2 send_message calls).
    assert bot.send_message.await_count == 2
    assert _reminded_at(session_factory, 1) is None  # not flagged -> will retry next sweep


def test_schedule_reminder_job(settings: Settings) -> None:
    job_queue = MagicMock()
    schedule_reminder_job(cast("JobQueue[ContextTypes.DEFAULT_TYPE]", job_queue), settings)
    job_queue.run_repeating.assert_called_once()
    kwargs = job_queue.run_repeating.call_args.kwargs
    assert kwargs["name"] == REMINDER_JOB_NAME
    assert kwargs["interval"] == settings.reminder_interval_minutes * 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reminder_job.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tigrinho.bot.reminder_job'`.

- [ ] **Step 3: Implement the job module**

Create `tigrinho/bot/reminder_job.py`:

```python
"""Pre-game betting reminders (COMPLETION.md §9.3).

A ``JobQueue.run_repeating`` job. Each sweep posts ONE group reminder for the soonest
unreminded kickoff slot due within ``reminder_lead_minutes`` of now — combining games that
share that exact kickoff time. Pure DB read + group post (no provider calls,
budget-independent). One bad cycle never kills the bot (§14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.keyboards import announcement_keyboard
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import Game, utcnow
from tigrinho.db.repositories import GameRepository
from tigrinho.domain.text_pt import escape, reminder_text
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.reminder_job")

REMINDER_JOB_NAME = "pre_game_reminder"


@dataclass(frozen=True, slots=True)
class _GameView:
    """Plain snapshot of a game for message building (decoupled from the session)."""

    fixture_id: int
    home_team_name: str
    away_team_name: str
    kickoff_local: datetime


def _view(game: Game) -> _GameView:
    return _GameView(
        fixture_id=game.fixture_id,
        home_team_name=game.home_team_name,
        away_team_name=game.away_team_name,
        kickoff_local=game.kickoff_local,
    )


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reminder sweep callback (§9.3). One bad cycle must not kill the bot (§14)."""
    app_context = get_app_context(context.application)
    try:
        await _run_reminder(app_context, context)
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("reminder_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Lembrete falhou: <code>{escape(str(exc))}</code>",
        )


async def _run_reminder(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()
    with app_context.session_factory() as session:
        games = GameRepository(session).list_due_for_reminder(now, settings.reminder_lead)
        views = [_view(g) for g in games]
    if not views:
        return

    text = reminder_text([(v.home_team_name, v.away_team_name, v.kickoff_local) for v in views])
    keyboard = announcement_keyboard(
        [(v.fixture_id, f"{v.home_team_name} x {v.away_team_name}") for v in views],
        settings.bot_username,
    )
    try:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramError as exc:
        _log.error("reminder_send_failed", error=str(exc), count=len(views))
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Falha ao enviar lembrete de {len(views)} jogo(s) (será reenviado): "
            f"<code>{escape(str(exc))}</code>",
        )
        return

    with app_context.session_factory() as session:
        GameRepository(session).mark_reminded([v.fixture_id for v in views], now)
        session.commit()
    _log.info("reminded", count=len(views))


def schedule_reminder_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the reminder sweep every ``reminder_interval_minutes`` (§9.3)."""
    job_queue.run_repeating(
        reminder_job,
        interval=settings.reminder_interval_minutes * 60,
        first=20,
        name=REMINDER_JOB_NAME,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reminder_job.py -v`
Expected: PASS (all seven tests).

- [ ] **Step 5: Commit**

```bash
git add tigrinho/bot/reminder_job.py tests/test_reminder_job.py
git commit -m "feat: pre-game reminder job (soonest-slot sweep, same-slot combine)"
```

---

## Task 7: Schedule the job in `post_init`

**Files:**
- Modify: `tigrinho/bot/app.py` (import + `post_init`)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

`tests/test_app.py` has no `post_init` test yet — add one. First extend the existing `from tigrinho.bot.app import (...)` block to include `post_init`:

```python
from tigrinho.bot.app import (
    GROUP_COMMANDS,
    PRIVATE_COMMANDS,
    StartupError,
    build_application,
    post_init,
    set_commands,
    validate_startup,
)
```

Add `from unittest.mock import patch` (extend the existing `from unittest.mock import AsyncMock, MagicMock`) and extend the runtime import to bring in `AnyApplication`:

```python
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AnyApplication, AppContext
```

Then append this test (it reuses the module-level `_bot` helper and the `app_context` fixture):

```python
async def test_post_init_schedules_all_jobs(app_context: AppContext) -> None:
    application = MagicMock()
    application.bot_data = {APP_CONTEXT_KEY: app_context}
    application.bot = _bot(app_context.settings.bot_username)
    application.job_queue = MagicMock()

    with (
        patch("tigrinho.bot.app.schedule_sync_job") as sync_mock,
        patch("tigrinho.bot.app.schedule_poll_job") as poll_mock,
        patch("tigrinho.bot.app.schedule_reminder_job") as reminder_mock,
    ):
        await post_init(cast(AnyApplication, application))

    sync_mock.assert_called_once()
    poll_mock.assert_called_once()
    reminder_mock.assert_called_once()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_app.py::test_post_init_schedules_all_jobs -v`
Expected: FAIL with `AttributeError: <module 'tigrinho.bot.app'> does not have the attribute 'schedule_reminder_job'` (the patch target doesn't exist yet).

- [ ] **Step 3: Wire the job into `post_init`**

In `tigrinho/bot/app.py`, add the import next to the other job imports:

```python
from tigrinho.bot.poll_job import schedule_poll_job
from tigrinho.bot.reminder_job import schedule_reminder_job
from tigrinho.bot.sync_job import schedule_sync_job
```

In `post_init`, inside the `if application.job_queue is not None:` block, add the call:

```python
    if application.job_queue is not None:
        schedule_sync_job(application.job_queue, app_context.settings)
        schedule_poll_job(application.job_queue, app_context.settings)
        schedule_reminder_job(application.job_queue, app_context.settings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tigrinho/bot/app.py tests/test_app.py
git commit -m "feat: schedule the reminder job at startup"
```

---

## Task 8: Documentation (COMPLETION.md §9.3 + PROGRESS.md)

**Files:**
- Modify: `COMPLETION.md` (new §9.3 after §9.2)
- Modify: `PROGRESS.md`

- [ ] **Step 1: Add §9.3 to `COMPLETION.md`**

Insert after the §9.2 section (before the `---` that precedes `## 10`):

```markdown
### 9.3 Pre-game reminders (`reminder_lead_minutes`, default 60)

A PTB **`JobQueue.run_repeating(interval=reminder_interval_minutes*60)`** job that nudges the group
to bet ~1h before kickoff. Each sweep:
1. Selects the **soonest due kickoff slot**: announced, unreminded, `SCHEDULED` games with
   `now < kickoff_utc <= now + reminder_lead`, narrowed to those sharing the soonest `kickoff_utc`.
   If none, **return without posting**.
2. Posts **one** consolidated reminder to the group (HTML, pt-BR), with one `🎯 Apostar` deep-link
   button per game in the slot — combining games that kick off at the **same time**.
3. Marks those games `reminded_at` **only on a successful send** (re-validated to skip games
   voided/rescheduled mid-flight); a failed send is retried on the next sweep and DMs the admin.

Makes **no provider calls** and is independent of the API budget (§7.3). Idempotent via
`reminded_at` (no double-posting across sweeps or restarts). On reschedule, `reminded_at` is cleared
(§9.1) so a moved game is reminded again before its new kickoff. Games at *different* kickoff times
each get their own reminder; only same-slot games are combined.
```

- [ ] **Step 2: Add a PROGRESS.md note**

`PROGRESS.md` records post-build work as dated `### YYYY-MM-DD — …` sections. Append a new one at the end of the file, matching that format:

```markdown
### 2026-06-15 — Feature: pre-game betting reminder (§9.3)

User request. New `JobQueue.run_repeating` reminder sweep (`bot/reminder_job.py`) posts one group
nudge ~1h before kickoff, combining games that share the **same kickoff time**. Soonest-due-slot
query (`GameRepository.list_due_for_reminder`), guarded `mark_reminded`, new nullable
`games.reminded_at` column + append-only migration `7f3a9c2b1e04`, announced-gate, and
`sync_fixtures` clears `reminded_at` on reschedule. Config: `reminder_lead_minutes` (60),
`reminder_interval_minutes` (10). Pure DB + group post (no provider calls). `/ajuda` unchanged
(no command/category/scoring/grading change). Design spec + multi-agent bug review (10 confirmed
findings folded in) under `docs/superpowers/`.
```

- [ ] **Step 3: Commit**

```bash
git add COMPLETION.md PROGRESS.md
git commit -m "docs: record §9.3 pre-game reminders"
```

---

## Task 9: Full gate run

- [ ] **Step 1: Run all four gates**

```bash
ruff check .
ruff format --check .
mypy --strict .
pytest
```

Expected: all four pass (zero ruff issues, formatting clean, no mypy errors, all tests green). If `ruff format --check` flags files, run `ruff format .`, re-run the gates, and amend the most relevant commit.

- [ ] **Step 2: Confirm the working tree is clean**

```bash
git status
```

Expected: clean tree, all work committed.

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** Task 1 = config; Task 2 = column+migration; Task 3 = soonest-slot query + guarded mark (review findings #1, #6, #9); Task 4 = reschedule reset (#7); Task 5 = message builder + weekday fix (#8); Task 6 = job incl. announced-gate (#10) and failure-retry; Task 7 = scheduling; Task 8 = docs. The `/ajuda` text is intentionally untouched (user decision; no command/category/scoring/grading change, so §11 does not require it).
- **Type consistency:** `list_due_for_reminder(now, lead)` and `mark_reminded(fixture_ids, when)` signatures match between Task 3, Task 6, and the tests. `reminder_text(games)` takes `Sequence[tuple[str, str, datetime]]` consistently in Task 5 and Task 6. `settings.reminder_lead` (a `timedelta`) from Task 1 is consumed in Task 6. `REMINDER_JOB_NAME` is defined in Task 6 and referenced in its test.
- **Naive UTC discipline:** every comparison in `list_due_for_reminder` is naive-UTC (`utcnow()` vs `kickoff_utc`); display uses `kickoff_local` via `format_kickoff_local` only.
