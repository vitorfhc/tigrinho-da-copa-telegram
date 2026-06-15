"""Request budget: a hard daily cap on provider calls (COMPLETION.md §7.3).

Wraps every provider call. The counter is keyed by the current date in ``api_budget_reset_tz``
(API-Football resets at 00:00 UTC), so it resets automatically when the budget date rolls over.
Per §7.3 the count is incremented **after a successful request**; a request is refused (raising
:class:`BudgetExceeded`) once the count reaches the cap.

The bot runs a single asyncio event loop with non-overlapping scheduled jobs, so check-then-record
is safe; the counter is committed in its own short transaction so a crash mid-job cannot lose it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, tzinfo

from sqlalchemy.orm import Session, sessionmaker

from tigrinho.db.repositories import ApiUsageRepository


class BudgetExceeded(Exception):
    """Raised when the daily provider-request cap has been reached (§7.3)."""

    def __init__(self, budget_date: date, cap: int) -> None:
        super().__init__(f"daily API budget reached: {cap} requests on {budget_date.isoformat()}")
        self.budget_date = budget_date
        self.cap = cap


def _utcnow_aware() -> datetime:
    return datetime.now(tz=UTC)


class RequestBudget:
    """Enforces a hard daily cap on provider requests, keyed by the reset timezone."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        daily_cap: int,
        reset_tz: tzinfo,
        clock: Callable[[], datetime] = _utcnow_aware,
    ) -> None:
        self._session_factory = session_factory
        self._daily_cap = daily_cap
        self._reset_tz = reset_tz
        self._clock = clock

    @property
    def daily_cap(self) -> int:
        return self._daily_cap

    def today(self) -> date:
        """The current budget date, in the configured reset timezone."""
        return self._clock().astimezone(self._reset_tz).date()

    def current_count(self) -> int:
        with self._session_factory() as session:
            return ApiUsageRepository(session).get_count(self.today())

    def remaining(self) -> int:
        return max(0, self._daily_cap - self.current_count())

    def is_exhausted(self) -> bool:
        return self.current_count() >= self._daily_cap

    def ensure_available(self) -> None:
        """Raise :class:`BudgetExceeded` if no requests remain for today."""
        budget_date = self.today()
        with self._session_factory() as session:
            count = ApiUsageRepository(session).get_count(budget_date)
        if count >= self._daily_cap:
            raise BudgetExceeded(budget_date, self._daily_cap)

    def record_request(self) -> int:
        """Increment today's counter in its own transaction; return the new count."""
        with self._session_factory() as session:
            new_count = ApiUsageRepository(session).increment(self.today())
            session.commit()
            return new_count

    async def guarded[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call`` only if budget remains, recording the request on success (§7.3)."""
        self.ensure_available()
        result = await call()
        self.record_request()
        return result
