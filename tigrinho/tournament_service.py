"""Bolãozinho (tournament) orchestration — Telegram-agnostic (Feature 7 / §22).

Shared by the bot handlers, the scheduled jobs, and the Typer CLI. Like the repositories it
**never commits** — the caller owns the unit of work. State invariants (the first-kickoff lock,
price freeze, open preconditions, join window) live here and raise :class:`TournamentError` with a
pt-BR message; *permission* checks are exposed as helpers (:func:`require_manage`) for the caller.
Money is integer cents; the resolution/announcement half lives further down (§7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import GameStatus, Tournament, TournamentStatus
from tigrinho.db.repositories import GameRepository, PlayerRepository, TournamentRepository
from tigrinho.domain.tournament import pot_cents, prize_cents


class TournamentError(Exception):
    """A user-facing (pt-BR) rejection of a bolãozinho action."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- permissions (F11) ---------------------------------------------------------------------------
def can_manage(tournament: Tournament, actor_id: int, admin_id: int) -> bool:
    """Only the creator (or the configured admin) may manage a bolãozinho."""
    return actor_id == tournament.created_by or actor_id == admin_id


def require_manage(tournament: Tournament, actor_id: int, admin_id: int) -> None:
    if not can_manage(tournament, actor_id, admin_id):
        raise TournamentError("Só quem criou o bolãozinho pode mexer nele.")


# --- locking (F1/F12) ----------------------------------------------------------------------------
def ensure_lock(session: Session, tournament: Tournament, now: datetime) -> None:
    """Persist the one-way first-kickoff lock once the earliest member game has kicked off.

    Persisting (rather than recomputing) means a later reschedule of the first game never unlocks
    an already-started bolãozinho (F12).
    """
    if tournament.locked_at is not None:
        return
    earliest = TournamentRepository(session).earliest_kickoff(tournament.id)
    if earliest is not None and now >= earliest:
        tournament.locked_at = now
        session.flush()


def is_locked(tournament: Tournament) -> bool:
    """Whether games/price/joins are frozen — call :func:`ensure_lock` first."""
    return tournament.locked_at is not None


# --- management ----------------------------------------------------------------------------------
def create_tournament(
    session: Session, *, name: str, entry_price_cents: int, created_by: int
) -> Tournament:
    return TournamentRepository(session).create(
        name=name, entry_price_cents=entry_price_cents, created_by=created_by
    )


def add_game(session: Session, tournament: Tournament, fixture_id: int, *, now: datetime) -> None:
    ensure_lock(session, tournament, now)
    if is_locked(tournament):
        raise TournamentError("O bolãozinho já começou — não dá mais pra mexer nos jogos.")
    game = GameRepository(session).get(fixture_id)
    if game is None or game.status is not GameStatus.SCHEDULED or game.kickoff_utc <= now:
        raise TournamentError("Só dá pra adicionar jogos que ainda não começaram.")
    TournamentRepository(session).add_game(tournament.id, fixture_id)


def remove_game(
    session: Session, tournament: Tournament, fixture_id: int, *, now: datetime
) -> None:
    ensure_lock(session, tournament, now)
    if is_locked(tournament):
        raise TournamentError("O bolãozinho já começou — não dá mais pra mexer nos jogos.")
    TournamentRepository(session).remove_game(tournament.id, fixture_id)


def set_price(
    session: Session, tournament: Tournament, entry_price_cents: int, *, now: datetime
) -> None:
    ensure_lock(session, tournament, now)
    if is_locked(tournament):
        raise TournamentError("O bolãozinho já começou — não dá mais pra mudar a entrada.")
    if TournamentRepository(session).count_entries(tournament.id) > 0:
        raise TournamentError("Não dá pra mudar a entrada depois que alguém já entrou.")
    tournament.entry_price_cents = entry_price_cents
    session.flush()


def open_tournament(session: Session, tournament: Tournament, *, now: datetime) -> None:
    repo = TournamentRepository(session)
    if tournament.status is not TournamentStatus.DRAFT:
        raise TournamentError("Esse bolãozinho não está em rascunho.")
    if tournament.entry_price_cents <= 0:
        raise TournamentError("Defina a entrada antes de abrir.")
    if not repo.list_game_ids(tournament.id):
        raise TournamentError("Adicione pelo menos um jogo antes de abrir.")
    earliest = repo.earliest_kickoff(tournament.id)
    if earliest is not None and now >= earliest:
        raise TournamentError("Um dos jogos já começou — não dá mais pra abrir.")
    tournament.status = TournamentStatus.OPEN
    tournament.opened_at = now
    session.flush()


def cancel_tournament(session: Session, tournament: Tournament) -> None:
    if tournament.status not in (TournamentStatus.DRAFT, TournamentStatus.OPEN):
        raise TournamentError("Esse bolãozinho já foi encerrado.")
    tournament.status = TournamentStatus.CANCELLED
    session.flush()


@dataclass(frozen=True, slots=True)
class JoinResult:
    """Outcome of a ``/entrar`` attempt (Telegram-agnostic)."""

    already: bool
    pot_cents: int
    prize_cents: int
    n_entrants: int
    game_ids: tuple[int, ...]


def join(
    session: Session,
    tournament: Tournament,
    *,
    telegram_id: int,
    display_name: str,
    now: datetime,
) -> JoinResult:
    """Enter a player (auto-creating the Player). Refuses once the bolãozinho is locked/closed."""
    if tournament.status is not TournamentStatus.OPEN:
        raise TournamentError("As entradas desse bolãozinho não estão abertas.")
    ensure_lock(session, tournament, now)
    if is_locked(tournament):
        raise TournamentError("As entradas fecharam — o primeiro jogo já começou.")
    PlayerRepository(session).get_or_create(telegram_id, display_name)
    repo = TournamentRepository(session)
    added = repo.add_entry(tournament.id, telegram_id)
    n = repo.count_entries(tournament.id)
    price = tournament.entry_price_cents
    return JoinResult(
        already=not added,
        pot_cents=pot_cents(n, price),
        prize_cents=prize_cents(n, price),
        n_entrants=n,
        game_ids=tuple(repo.list_game_ids(tournament.id)),
    )
