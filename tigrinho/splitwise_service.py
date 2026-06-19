"""Bolãozinho → Splitwise registration logic (Feature 8 / §23), Telegram-agnostic.

Decides, from DB state alone, what (if anything) should be pushed to Splitwise for a finished
bolãozinho. The actual async API call + persistence lives in the bot layer; this module never
commits and performs no network I/O — it returns a :class:`SplitwiseRegistration` describing a
create or an update, or ``None`` to skip. The ledger math is the pure ``domain.splitwise_ledger``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from tigrinho.db.models import Player, SplitwiseMode, Tournament, TournamentStatus
from tigrinho.db.repositories import PlayerRepository, TournamentRepository
from tigrinho.domain.splitwise_ledger import build_ledger, ledger_cost_cents
from tigrinho.domain.text_pt import splitwise_expense_description
from tigrinho.domain.tournament import compute_outcome
from tigrinho.providers.splitwise import ExpenseShare
from tigrinho.tournament_service import signature_of


def initial_splitwise_mode(status: TournamentStatus) -> SplitwiseMode:
    """The mode an *existing* bolãozinho gets at deploy: closed → EXCLUDED, else MANUAL (§23)."""
    if status in (TournamentStatus.FINISHED, TournamentStatus.CANCELLED):
        return SplitwiseMode.EXCLUDED
    return SplitwiseMode.MANUAL


@dataclass(frozen=True, slots=True)
class SplitwiseRegistration:
    """A pending create (``expense_id is None``) or update of one bolãozinho's Splitwise expense."""

    tournament_id: int
    expense_id: int | None
    cost_cents: int
    description: str
    shares: tuple[ExpenseShare, ...]
    signature: str
    is_correction: bool


def all_entrants_linked(session: Session, tournament_id: int) -> bool:
    """Whether every entrant has a ``splitwise_user_id`` (and there is at least one entrant)."""
    repo = TournamentRepository(session)
    players = PlayerRepository(session)
    ids = repo.entry_ids(tournament_id)
    if not ids:
        return False
    return all(_linked_user_id(players.get(tid)) is not None for tid in ids)


def _linked_user_id(player: Player | None) -> int | None:
    return player.splitwise_user_id if player is not None else None


def build_registration(session: Session, tournament_id: int) -> SplitwiseRegistration | None:
    """Compute the create/update to push for ``tournament_id``, or ``None`` to skip.

    Skips when: the bolãozinho is missing or EXCLUDED · there is no scorable result · any entrant is
    unlinked · the settlement is zero-cost (lone entrant / full tie) · the outcome signature is
    already synced. Otherwise returns a balanced expense; ``expense_id`` set ⇒ this is a correction.
    """
    repo = TournamentRepository(session)
    players = PlayerRepository(session)
    tournament = repo.get(tournament_id)
    if tournament is None or tournament.splitwise_mode is SplitwiseMode.EXCLUDED:
        return None

    outcome = compute_outcome(repo.standings(tournament_id), tournament.entry_price_cents)
    if not outcome.has_result:
        return None

    entrant_ids = repo.entry_ids(tournament_id)
    user_id_by_tid: dict[int, int] = {}
    name_by_tid: dict[int, str] = {}
    for tid in entrant_ids:
        player = players.get(tid)
        if player is None or player.splitwise_user_id is None:
            return None
        user_id_by_tid[tid] = player.splitwise_user_id
        name_by_tid[tid] = player.display_name

    ledger = build_ledger(entrant_ids, outcome.winner_ids, tournament.entry_price_cents)
    if not ledger:  # cost 0 (lone entrant or full tie) — nothing changes hands
        return None

    signature = signature_of(outcome)
    if signature == tournament.splitwise_synced_signature:
        return None

    shares = tuple(
        ExpenseShare(
            user_id=user_id_by_tid[tid],
            paid_cents=share.paid_cents,
            owed_cents=share.owed_cents,
        )
        for tid, share in ledger.items()
    )
    winner_names = [name_by_tid[tid] for tid in outcome.winner_ids]
    description = splitwise_expense_description(name=tournament.name, winners=winner_names)
    return SplitwiseRegistration(
        tournament_id=tournament_id,
        expense_id=tournament.splitwise_expense_id,
        cost_cents=ledger_cost_cents(ledger),
        description=description,
        shares=shares,
        signature=signature,
        is_correction=tournament.splitwise_expense_id is not None,
    )


def mark_synced(tournament: Tournament, *, expense_id: int, signature: str) -> None:
    """Record that ``tournament`` is registered in Splitwise at ``signature`` (caller commits)."""
    tournament.splitwise_expense_id = expense_id
    tournament.splitwise_synced_signature = signature


def finished_auto_tournaments(session: Session) -> list[Tournament]:
    """FINISHED AUTO bolãozinhos — the sweep retries registration for unsynced ones (§23)."""
    repo = TournamentRepository(session)
    return [
        t
        for t in repo.list_by_status(TournamentStatus.FINISHED)
        if t.splitwise_mode is SplitwiseMode.AUTO
    ]


def manual_ready_to_notify(session: Session) -> list[Tournament]:
    """FINISHED MANUAL bolãozinhos now fully linked and not yet registered/notified (§23 sweep)."""
    repo = TournamentRepository(session)
    return [
        t
        for t in repo.list_by_status(TournamentStatus.FINISHED)
        if t.splitwise_mode is SplitwiseMode.MANUAL
        and t.splitwise_expense_id is None
        and t.splitwise_admin_notified_at is None
        and all_entrants_linked(session, t.id)
    ]


def manual_registerable(session: Session) -> list[Tournament]:
    """FINISHED MANUAL bolãozinhos fully linked and not yet registered — the admin picker (§23)."""
    repo = TournamentRepository(session)
    return [
        t
        for t in repo.list_by_status(TournamentStatus.FINISHED)
        if t.splitwise_mode is SplitwiseMode.MANUAL
        and t.splitwise_expense_id is None
        and all_entrants_linked(session, t.id)
    ]
