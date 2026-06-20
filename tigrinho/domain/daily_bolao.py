"""Pure scoring, selection, and date-window helpers for the daily AI bolãozinho (§24).

PURE: no I/O, clock, or DB. The interest "score" is the count of the six binary criteria that
are true — the only numeric value, and it is derived purely from booleans (never emitted by the
model). Selection ranks the scored candidates by ``(interest desc, kickoff asc)`` and takes the
top ``limit``, dropping any fixture id the model invented (not a real candidate).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class Candidate:
    """A fixture eligible for tomorrow's pool: its id and (naive UTC) kickoff."""

    fixture_id: int
    kickoff_utc: datetime


@dataclass(frozen=True, slots=True)
class InterestCriteria:
    """The six yes/no grades the model assigns to one game."""

    decisive: bool
    quality_matchup: bool
    rivalry_or_storyline: bool
    star_power: bool
    competitive_balance: bool
    goal_potential: bool


def interest(c: InterestCriteria) -> int:
    """The interest score: how many of the six binary criteria are true (0–6)."""
    return sum(
        (
            c.decisive,
            c.quality_matchup,
            c.rivalry_or_storyline,
            c.star_power,
            c.competitive_balance,
            c.goal_potential,
        )
    )


def rank_and_select(
    candidates: Sequence[Candidate], scores: Mapping[int, int], *, limit: int = 2
) -> list[int]:
    """Pick up to ``limit`` fixture ids: highest interest first, earliest kickoff breaks ties.

    Only fixture ids present in BOTH ``scores`` and ``candidates`` are considered (a model that
    invents an id, or omits a candidate, simply doesn't place that id). Returns ``[]`` when the
    intersection is empty — the caller treats that as a failure (no fallback).
    """
    by_id = {c.fixture_id: c for c in candidates}
    ranked = sorted(
        ((scores[fid], by_id[fid].kickoff_utc, fid) for fid in scores if fid in by_id),
        key=lambda t: (-t[0], t[1]),
    )
    return [fid for _, _, fid in ranked[:limit]]


def local_day_window_utc(target_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The ``[start, end)`` of ``target_date`` (local) as naive-UTC datetimes (DST-safe).

    The day boundary is built in LOCAL time and the +1 day is added in local time *before*
    converting to UTC, so a DST transition (a 23h/25h local day) is handled correctly.
    """
    start_local = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(UTC).replace(tzinfo=None)
    end_utc = end_local.astimezone(UTC).replace(tzinfo=None)
    return start_utc, end_utc
