"""Pure Splitwise ledger math: turn a bolãozinho result into one balanced expense (§23).

No I/O, clock, or DB — deterministic and fully covered. Money is integer **cents**. The settlement
is "losers fund winners": every loser owes their entry stake, the winners split that pot. For a
single winner this is exactly the announced prize ((n−1)×entry); for ties it splits the losers'
stakes equally (the exact zero-sum settlement) — which may differ by a few cents from §22's
display-only "prize ÷ k" abstraction, by design. A Splitwise expense must balance exactly, so the
per-user paid/owed shares always satisfy ``Σpaid == Σowed == cost``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LedgerShare:
    """One participant's side of the expense, in integer cents (``net = paid − owed``)."""

    paid_cents: int
    owed_cents: int


def build_ledger(
    entrant_ids: Sequence[int],
    winner_ids: Sequence[int],
    entry_price_cents: int,
) -> dict[int, LedgerShare]:
    """Per-participant paid/owed cents for the expense, keyed by the caller's ids.

    Losers each owe ``entry_price_cents`` (paid 0); winners split the losers' total stake equally,
    the ``cost % k`` leftover cents going one-per-winner to the lowest ids (paid that, owed 0). When
    there are no losers (lone entrant or a full tie) the cost is 0 and the ledger is empty — nothing
    changes hands, so the caller should register nothing.
    """
    winners = set(winner_ids)
    losers = [i for i in entrant_ids if i not in winners]
    cost = len(losers) * entry_price_cents
    if cost == 0:
        return {}

    shares: dict[int, LedgerShare] = {
        i: LedgerShare(paid_cents=0, owed_cents=entry_price_cents) for i in losers
    }

    ordered_winners = sorted(winners)
    base, remainder = divmod(cost, len(ordered_winners))
    for index, winner_id in enumerate(ordered_winners):
        paid = base + (1 if index < remainder else 0)
        shares[winner_id] = LedgerShare(paid_cents=paid, owed_cents=0)
    return shares


def ledger_cost_cents(ledger: Mapping[int, LedgerShare]) -> int:
    """The expense total = sum of owed shares (equivalently, sum of paid shares)."""
    return sum(share.owed_cents for share in ledger.values())


def cents_to_amount(cents: int) -> str:
    """Render integer cents as the Splitwise API's 2-decimal amount string (no float math)."""
    whole, frac = divmod(cents, 100)
    return f"{whole}.{frac:02d}"
