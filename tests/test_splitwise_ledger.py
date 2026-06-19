"""Tests for the pure Splitwise ledger math (§23) — 100% line+branch coverage."""

from __future__ import annotations

from tigrinho.domain.splitwise_ledger import (
    LedgerShare,
    build_ledger,
    cents_to_amount,
    ledger_cost_cents,
)


def _assert_balanced(ledger: dict[int, LedgerShare]) -> None:
    paid = sum(s.paid_cents for s in ledger.values())
    owed = sum(s.owed_cents for s in ledger.values())
    assert paid == owed == ledger_cost_cents(ledger)


def test_single_winner_losers_each_owe_entry() -> None:
    ledger = build_ledger([1, 2, 3], [1], 1000)
    assert ledger[2] == LedgerShare(paid_cents=0, owed_cents=1000)
    assert ledger[3] == LedgerShare(paid_cents=0, owed_cents=1000)
    # The lone winner is owed the whole pot of losers' stakes = the announced prize.
    assert ledger[1] == LedgerShare(paid_cents=2000, owed_cents=0)
    assert ledger_cost_cents(ledger) == 2000
    _assert_balanced(ledger)


def test_two_way_tie_splits_losers_stake_with_odd_cent_to_first() -> None:
    # 3 entrants, 2 winners, entry 1001 → one loser owes 1001; split between winners 1 & 2.
    ledger = build_ledger([1, 2, 3], [2, 1], 1001)
    assert ledger[3] == LedgerShare(paid_cents=0, owed_cents=1001)
    # 1001 // 2 = 500, remainder 1 → lowest id (1) gets the extra cent.
    assert ledger[1] == LedgerShare(paid_cents=501, owed_cents=0)
    assert ledger[2] == LedgerShare(paid_cents=500, owed_cents=0)
    assert ledger_cost_cents(ledger) == 1001
    _assert_balanced(ledger)


def test_lone_entrant_has_no_losers_so_empty_ledger() -> None:
    assert build_ledger([1], [1], 1000) == {}


def test_full_tie_no_losers_empty_ledger() -> None:
    assert build_ledger([1, 2], [1, 2], 1000) == {}


def test_three_way_tie_remainder_two_cents() -> None:
    # 5 entrants, 3 winners, entry 1000 → 2 losers owe 1000 each → cost 2000.
    # 2000 // 3 = 666 r 2 → winners (lowest two ids) get the extra cent each.
    ledger = build_ledger([1, 2, 3, 4, 5], [1, 2, 3], 1000)
    assert ledger[1] == LedgerShare(paid_cents=667, owed_cents=0)
    assert ledger[2] == LedgerShare(paid_cents=667, owed_cents=0)
    assert ledger[3] == LedgerShare(paid_cents=666, owed_cents=0)
    assert ledger[4] == LedgerShare(paid_cents=0, owed_cents=1000)
    assert ledger[5] == LedgerShare(paid_cents=0, owed_cents=1000)
    assert ledger_cost_cents(ledger) == 2000
    _assert_balanced(ledger)


def test_cents_to_amount_formats_two_decimals() -> None:
    assert cents_to_amount(9000) == "90.00"
    assert cents_to_amount(501) == "5.01"
    assert cents_to_amount(0) == "0.00"
    assert cents_to_amount(100) == "1.00"
