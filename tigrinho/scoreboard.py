"""Scoreboard computation (COMPLETION.md §10).

PURE and rebuildable: :func:`rank` derives the standings from a flat list of settled-bet records,
so the bot and the CLI produce identical results. Tie-breaks (§10): (1) points desc,
(2) exact-score hits desc, (3) total correct bets desc, (4) earliest ``players.created_at``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class BetRecord:
    """One settled bet, projected for ranking."""

    telegram_id: int
    display_name: str
    created_at: datetime
    points: int
    is_correct: bool
    is_exact_score_hit: bool


@dataclass(frozen=True, slots=True)
class RankEntry:
    rank: int
    telegram_id: int
    display_name: str
    points: int
    exact_hits: int
    correct: int


@dataclass(slots=True)
class _Acc:
    display_name: str
    created_at: datetime
    points: int = 0
    exact_hits: int = 0
    correct: int = 0


def rank(records: Sequence[BetRecord]) -> list[RankEntry]:
    """Aggregate settled bets into ranked standings (highest first), applying the tie-breaks."""
    accumulators: dict[int, _Acc] = {}
    for record in records:
        acc = accumulators.get(record.telegram_id)
        if acc is None:
            acc = _Acc(display_name=record.display_name, created_at=record.created_at)
            accumulators[record.telegram_id] = acc
        acc.points += record.points
        acc.correct += int(record.is_correct)
        acc.exact_hits += int(record.is_exact_score_hit)
        acc.created_at = min(acc.created_at, record.created_at)

    ordered = sorted(
        accumulators.items(),
        key=lambda item: (
            -item[1].points,
            -item[1].exact_hits,
            -item[1].correct,
            item[1].created_at,
        ),
    )
    return [
        RankEntry(
            rank=index + 1,
            telegram_id=telegram_id,
            display_name=acc.display_name,
            points=acc.points,
            exact_hits=acc.exact_hits,
            correct=acc.correct,
        )
        for index, (telegram_id, acc) in enumerate(ordered)
    ]


def week_bounds(now_local: datetime) -> tuple[datetime, datetime]:
    """Current Mon 00:00 → next Mon 00:00 (in local time); the weekly window resets Monday (§10)."""
    monday = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=7)


def in_current_week(kickoff_local: datetime, now_local: datetime) -> bool:
    start, end = week_bounds(now_local)
    return start <= kickoff_local < end
