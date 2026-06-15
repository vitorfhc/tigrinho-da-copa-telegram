"""Tests for the pure scoreboard computation + tie-breaks (COMPLETION.md §10, §16)."""

from __future__ import annotations

from datetime import datetime, timedelta

from tigrinho.scoreboard import BetRecord, in_current_week, rank, week_bounds

_C = datetime(2026, 6, 1, 12, 0)


def _rec(
    telegram_id: int,
    points: int,
    *,
    created: datetime = _C,
    correct: bool = True,
    exact: bool = False,
) -> BetRecord:
    return BetRecord(
        telegram_id=telegram_id,
        display_name=f"p{telegram_id}",
        created_at=created,
        points=points,
        is_correct=correct,
        is_exact_score_hit=exact,
    )


def test_rank_orders_by_points_and_aggregates() -> None:
    entries = rank([_rec(1, 5), _rec(1, 2), _rec(2, 4)])
    assert [(e.telegram_id, e.points, e.rank) for e in entries] == [(1, 7, 1), (2, 4, 2)]


def test_tiebreak_exact_hits() -> None:
    entries = rank([_rec(1, 5, exact=False), _rec(2, 5, exact=True)])
    assert [e.telegram_id for e in entries] == [2, 1]


def test_tiebreak_total_correct() -> None:
    # both 5 points, 0 exact hits; player 1 has 2 correct bets, player 2 has 1
    entries = rank([_rec(1, 3, correct=True), _rec(1, 2, correct=True), _rec(2, 5, correct=True)])
    assert [e.telegram_id for e in entries] == [1, 2]


def test_tiebreak_earliest_created_at() -> None:
    early = datetime(2026, 1, 1)
    late = datetime(2026, 2, 1)
    entries = rank([_rec(1, 5, created=late), _rec(2, 5, created=early)])
    assert [e.telegram_id for e in entries] == [2, 1]


def test_week_bounds_and_membership() -> None:
    now = datetime(2026, 6, 17, 12, 0)  # a Wednesday
    start, end = week_bounds(now)
    assert start.weekday() == 0  # Monday
    assert start <= now < end
    assert end - start == timedelta(days=7)
    assert in_current_week(start, now) is True
    assert in_current_week(end - timedelta(seconds=1), now) is True
    assert in_current_week(end, now) is False  # next week
    assert in_current_week(start - timedelta(seconds=1), now) is False  # last week
