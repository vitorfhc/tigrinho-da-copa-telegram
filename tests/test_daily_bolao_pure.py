"""Pure tests for the daily-bolãozinho scoring/selection/window helpers (§24)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tigrinho.domain.daily_bolao import (
    Candidate,
    InterestCriteria,
    interest,
    local_day_window_utc,
    rank_and_select,
)


def _crit(n_true: int) -> InterestCriteria:
    flags = [i < n_true for i in range(6)]
    return InterestCriteria(*flags)


def test_interest_counts_true_grades() -> None:
    assert interest(InterestCriteria(False, False, False, False, False, False)) == 0
    assert interest(InterestCriteria(True, True, True, True, True, True)) == 6
    assert interest(InterestCriteria(True, False, True, False, True, False)) == 3


def _cand(fid: int, hour: int) -> Candidate:
    return Candidate(fixture_id=fid, kickoff_utc=datetime(2026, 6, 21, hour, 0))


def test_rank_and_select_picks_top_two_by_interest() -> None:
    candidates = [_cand(1, 13), _cand(2, 16), _cand(3, 19)]
    scores = {1: 2, 2: 5, 3: 4}
    assert rank_and_select(candidates, scores) == [2, 3]


def test_rank_and_select_tie_breaks_on_earlier_kickoff() -> None:
    candidates = [_cand(1, 19), _cand(2, 13), _cand(3, 16)]
    scores = {1: 4, 2: 4, 3: 4}
    assert rank_and_select(candidates, scores) == [2, 3]


def test_rank_and_select_drops_hallucinated_ids() -> None:
    candidates = [_cand(1, 13), _cand(2, 16)]
    scores = {1: 3, 2: 2, 999: 6}  # 999 is not a real candidate
    assert rank_and_select(candidates, scores) == [1, 2]


def test_rank_and_select_partial_coverage_returns_subset() -> None:
    candidates = [_cand(1, 13), _cand(2, 16), _cand(3, 19)]
    scores = {2: 4}  # Gemini scored only one of three
    assert rank_and_select(candidates, scores) == [2]


def test_rank_and_select_empty_intersection_returns_empty() -> None:
    candidates = [_cand(1, 13)]
    assert rank_and_select(candidates, {999: 6}) == []


def test_rank_and_select_respects_limit() -> None:
    candidates = [_cand(1, 13), _cand(2, 16), _cand(3, 19)]
    scores = {1: 1, 2: 2, 3: 3}
    assert rank_and_select(candidates, scores, limit=1) == [3]


def test_local_day_window_utc_is_next_local_day_in_utc() -> None:
    tz = ZoneInfo("America/Sao_Paulo")  # UTC-3, no DST
    start, end = local_day_window_utc(date(2026, 6, 21), tz)
    assert start == datetime(2026, 6, 21, 3, 0)  # 00:00 local == 03:00 UTC
    assert end == datetime(2026, 6, 22, 3, 0)
    assert start.tzinfo is None and end.tzinfo is None
