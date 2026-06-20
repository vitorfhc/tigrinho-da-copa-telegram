"""Pure bolãozinho (tournament) math: pot, prize, winners, and input parsing (Feature 7 / §22).

No I/O, clock, or DB — deterministic and fully covered (the DoD coverage gate, §0/§2). Money is
integer **cents**. The winner does **not** win their own stake, so the prize is the pot minus one
entry, split equally among tied winners with any leftover cent surfaced as a "sobra".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass


def pot_cents(n_entrants: int, entry_price_cents: int) -> int:
    """Total collected = entrants × entry price (uniform price; frozen at the first entry)."""
    return n_entrants * entry_price_cents


def prize_cents(n_entrants: int, entry_price_cents: int) -> int:
    """Pot minus one entry — the winner keeps their own stake (0 for a lone/empty entrant set)."""
    return max(0, (n_entrants - 1) * entry_price_cents)


@dataclass(frozen=True, slots=True)
class PrizeSplit:
    """How a prize divides among tied winners (integer cents; ``remainder`` is the leftover)."""

    per_winner_cents: int
    remainder_cents: int


def split_prize(prize_cents_total: int, n_winners: int) -> PrizeSplit:
    """Divide a prize evenly; the remainder (unassigned cents) is shown as a "sobra"."""
    if n_winners < 1:
        raise ValueError("n_winners must be >= 1")
    per = prize_cents_total // n_winners
    return PrizeSplit(per, prize_cents_total - per * n_winners)


@dataclass(frozen=True, slots=True)
class Winners:
    """The set of tied winners (telegram ids sorted ascending) and their shared score."""

    telegram_ids: tuple[int, ...]
    score: int


def determine_winners(scores: Mapping[int, int]) -> Winners | None:
    """The entrants tied at the top score (pure equality; no tie-breaks). None if no entrants."""
    if not scores:
        return None
    top = max(scores.values())
    ids = tuple(sorted(tid for tid, score in scores.items() if score == top))
    return Winners(ids, top)


@dataclass(frozen=True, slots=True)
class TournamentOutcome:
    """Everything needed to announce a finished bolãozinho (Telegram-agnostic)."""

    has_result: bool
    pot_cents: int
    prize_cents: int
    winner_ids: tuple[int, ...]
    winning_score: int
    per_winner_cents: int
    remainder_cents: int


def compute_outcome(scores: Mapping[int, int], entry_price_cents: int) -> TournamentOutcome:
    """Combine pot/prize/winner math into a single outcome (``has_result`` False if no entrants)."""
    n = len(scores)
    pot = pot_cents(n, entry_price_cents)
    prize = prize_cents(n, entry_price_cents)
    winners = determine_winners(scores)
    if winners is None:
        return TournamentOutcome(False, pot, prize, (), 0, 0, 0)
    split = split_prize(prize, len(winners.telegram_ids))
    return TournamentOutcome(
        has_result=True,
        pot_cents=pot,
        prize_cents=prize,
        winner_ids=winners.telegram_ids,
        winning_score=winners.score,
        per_winner_cents=split.per_winner_cents,
        remainder_cents=split.remainder_cents,
    )


_PRICE_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")


def parse_price_to_cents(raw: str) -> int:
    """Parse a decimal price (``10``, ``10,50``, ``10.50``) to cents; ValueError if invalid/≤0."""
    stripped = raw.strip()
    if not _PRICE_RE.match(stripped):
        raise ValueError(f"invalid price: {raw!r}")
    whole, _, frac = stripped.replace(".", ",").partition(",")
    cents = int(whole) * 100 + (int(frac.ljust(2, "0")) if frac else 0)
    if cents <= 0:
        raise ValueError("price must be > 0")
    return cents
