"""Tests for RequestBudget hard-stop + reset behavior (COMPLETION.md §7.3, §16)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tigrinho.providers.budget import BudgetExceeded, RequestBudget


def _budget(
    session_factory: sessionmaker[Session],
    *,
    cap: int = 3,
    reset_tz: ZoneInfo | None = None,
    now: datetime | None = None,
) -> RequestBudget:
    clock_value = now or datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    return RequestBudget(
        session_factory,
        daily_cap=cap,
        reset_tz=reset_tz or ZoneInfo("UTC"),
        clock=lambda: clock_value,
    )


def test_starts_empty(session_factory: sessionmaker[Session]) -> None:
    budget = _budget(session_factory)
    assert budget.current_count() == 0
    assert budget.remaining() == 3
    assert budget.is_exhausted() is False
    budget.ensure_available()  # does not raise


def test_record_increments_until_cap(session_factory: sessionmaker[Session]) -> None:
    budget = _budget(session_factory, cap=3)
    assert budget.record_request() == 1
    assert budget.record_request() == 2
    assert budget.record_request() == 3
    assert budget.remaining() == 0
    assert budget.is_exhausted() is True
    with pytest.raises(BudgetExceeded):
        budget.ensure_available()


async def test_guarded_runs_and_records(session_factory: sessionmaker[Session]) -> None:
    budget = _budget(session_factory, cap=2)
    called = False

    async def call() -> str:
        nonlocal called
        called = True
        return "ok"

    result = await budget.guarded(call)
    assert result == "ok"
    assert called is True
    assert budget.current_count() == 1


async def test_guarded_refuses_at_cap_without_calling(
    session_factory: sessionmaker[Session],
) -> None:
    budget = _budget(session_factory, cap=1)
    budget.record_request()  # exhaust
    called = False

    async def call() -> str:
        nonlocal called
        called = True
        return "ok"

    with pytest.raises(BudgetExceeded):
        await budget.guarded(call)
    assert called is False


def test_counter_resets_on_date_rollover(session_factory: sessionmaker[Session]) -> None:
    day1 = datetime(2026, 6, 15, 23, 0, tzinfo=UTC)
    day2 = datetime(2026, 6, 16, 1, 0, tzinfo=UTC)
    clock = {"now": day1}
    budget = RequestBudget(
        session_factory,
        daily_cap=2,
        reset_tz=ZoneInfo("UTC"),
        clock=lambda: clock["now"],
    )
    budget.record_request()
    budget.record_request()
    assert budget.is_exhausted() is True

    clock["now"] = day2
    assert budget.current_count() == 0  # new budget day
    assert budget.remaining() == 2
    budget.ensure_available()


def test_budget_date_uses_reset_timezone(session_factory: sessionmaker[Session]) -> None:
    # 01:00 UTC on the 16th is still 22:00 on the 15th in São Paulo (UTC-3).
    budget = _budget(
        session_factory,
        reset_tz=ZoneInfo("America/Sao_Paulo"),
        now=datetime(2026, 6, 16, 1, 0, tzinfo=UTC),
    )
    assert budget.today() == date(2026, 6, 15)
