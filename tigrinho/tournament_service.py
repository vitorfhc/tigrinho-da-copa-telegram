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

from tigrinho.db.models import GameStatus, SplitwiseMode, Tournament, TournamentStatus, utcnow
from tigrinho.db.repositories import GameRepository, PlayerRepository, TournamentRepository
from tigrinho.domain.tournament import TournamentOutcome, compute_outcome, pot_cents, prize_cents


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


def open_tournament(
    session: Session, tournament: Tournament, *, now: datetime, splitwise_enabled: bool = False
) -> None:
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
    # Stamp AUTO when the Splitwise feature is on at open: the join guard then ensures every entrant
    # links, so the result can auto-register. Otherwise it stays MANUAL (admin-triggered). §23.
    if splitwise_enabled:
        tournament.splitwise_mode = SplitwiseMode.AUTO
    session.flush()


def cancel_tournament(
    session: Session, tournament: Tournament, *, reason: str | None = None
) -> None:
    if tournament.status not in (TournamentStatus.DRAFT, TournamentStatus.OPEN):
        raise TournamentError("Esse bolãozinho já foi encerrado.")
    tournament.status = TournamentStatus.CANCELLED
    tournament.cancel_reason = reason
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
    player = PlayerRepository(session).get_or_create(telegram_id, display_name)
    # Splitwise join guard (§23): an AUTO bolãozinho requires every entrant linked so its result can
    # auto-register and the expense always balances. MANUAL/EXCLUDED keep frictionless joining.
    if tournament.splitwise_mode is SplitwiseMode.AUTO and player.splitwise_user_id is None:
        raise TournamentError("Vincule seu Splitwise antes de entrar.")
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


# --- resolution, outcome & announcements (§7) ----------------------------------------------------
@dataclass(frozen=True, slots=True)
class WinnerLine:
    """One winner in a result announcement."""

    telegram_id: int
    display_name: str
    score: int


@dataclass(frozen=True, slots=True)
class TournamentWinnerAnnouncement:
    """A bolãozinho concluded with a winner (or tied winners)."""

    tournament_id: int
    name: str
    n_entrants: int
    pot_cents: int
    prize_cents: int
    winners: tuple[WinnerLine, ...]
    per_winner_cents: int
    remainder_cents: int
    is_correction: bool


@dataclass(frozen=True, slots=True)
class TournamentNoResultAnnouncement:
    """A bolãozinho ended with no scorable result (all games void, or no entrants)."""

    tournament_id: int
    name: str


@dataclass(frozen=True, slots=True)
class TournamentPartialAnnouncement:
    """Standings so far — posted once per newly-finished member game while running (§22.4)."""

    tournament_id: int
    name: str
    settled_count: int
    total_games: int
    n_entrants: int
    pot_cents: int
    prize_cents: int
    standings: tuple[tuple[str, int], ...]  # ranked (display_name, points)


TournamentAnnouncement = (
    TournamentWinnerAnnouncement | TournamentNoResultAnnouncement | TournamentPartialAnnouncement
)

_NO_RESULT_SIGNATURE = "NORESULT"
# Cap on the standings rows shown in a partial-placar post (mirrors the details mini-standings).
_MAX_PARTIAL_STANDINGS = 15


def signature_of(outcome: TournamentOutcome) -> str:
    """A stable hash of the announced outcome (winners + payout + score), to detect re-grades."""
    ids = ",".join(str(i) for i in outcome.winner_ids)
    return f"W:{ids}|{outcome.per_winner_cents}|{outcome.remainder_cents}|{outcome.winning_score}"


def _partial_announcement(
    session: Session,
    repo: TournamentRepository,
    players: PlayerRepository,
    tournament: Tournament,
) -> TournamentPartialAnnouncement | None:
    """Standings post for a still-running bolãozinho, once per newly-finished member game (§22.4).

    Fires only while the bolãozinho is OPEN and not yet fully resolved (the final game's resolution
    posts the winner instead). Idempotent via the persisted ``partial_announced_count`` watermark —
    a re-grade or void of an already-counted game never re-posts. Returns ``None`` when nothing new
    settled or there is no one to rank.
    """
    if tournament.status is not TournamentStatus.OPEN:
        return None
    settled_count = repo.count_settled_games(tournament.id)
    n_entrants = repo.count_entries(tournament.id)
    if settled_count <= tournament.partial_announced_count or n_entrants == 0:
        return None
    tournament.partial_announced_count = settled_count
    session.flush()
    ranked = sorted(repo.standings(tournament.id).items(), key=lambda kv: (-kv[1], kv[0]))
    standings = tuple(
        ((p.display_name if (p := players.get(tid)) is not None else str(tid)), points)
        for tid, points in ranked[:_MAX_PARTIAL_STANDINGS]
    )
    price = tournament.entry_price_cents
    return TournamentPartialAnnouncement(
        tournament_id=tournament.id,
        name=tournament.name,
        settled_count=settled_count,
        total_games=len(repo.list_game_ids(tournament.id)),
        n_entrants=n_entrants,
        pot_cents=pot_cents(n_entrants, price),
        prize_cents=prize_cents(n_entrants, price),
        standings=standings,
    )


def on_game_resolved(session: Session, fixture_id: int) -> list[TournamentAnnouncement]:
    """Re-evaluate every bolãozinho containing ``fixture_id`` after any game state change (§7).

    Fired from settle, void, and un-void/reschedule paths plus the sweep. Announces a winner (or a
    no-result) the first time all member games are resolved, corrects an already-announced result
    when a re-grade flips it (F8), and revives an auto-cancelled (all-void) bolãozinho whose games
    later come back to life (F5). Idempotent — a recomputed signature equal to the stored one is a
    no-op. Manually cancelled bolãozinhos (no stored signature) are left untouched.
    """
    repo = TournamentRepository(session)
    players = PlayerRepository(session)
    announcements: list[TournamentAnnouncement] = []

    for tournament in repo.tournaments_for_game(fixture_id):
        # A creator/admin cancellation has no stored signature — never revive or announce it.
        if tournament.status is TournamentStatus.CANCELLED and tournament.result_signature is None:
            continue
        if not repo.all_games_resolved(tournament.id):
            partial = _partial_announcement(session, repo, players, tournament)
            if partial is not None:
                announcements.append(partial)
            continue

        no_result = repo.count_entries(tournament.id) == 0 or repo.has_only_void_games(
            tournament.id
        )
        if no_result:
            if tournament.result_signature == _NO_RESULT_SIGNATURE:
                continue  # already announced as no-result
            tournament.status = TournamentStatus.CANCELLED
            tournament.result_announced_at = utcnow()
            tournament.result_signature = _NO_RESULT_SIGNATURE
            session.flush()
            announcements.append(TournamentNoResultAnnouncement(tournament.id, tournament.name))
            continue

        outcome = compute_outcome(repo.standings(tournament.id), tournament.entry_price_cents)
        signature = signature_of(outcome)
        already_announced = tournament.result_announced_at is not None
        if already_announced and tournament.result_signature == signature:
            continue  # nothing moved — idempotent

        is_correction = already_announced
        tournament.status = TournamentStatus.FINISHED
        tournament.result_announced_at = utcnow()
        tournament.result_signature = signature
        if is_correction:
            tournament.correction_count += 1
        session.flush()

        winners = tuple(
            WinnerLine(
                telegram_id=tid,
                display_name=(p.display_name if (p := players.get(tid)) is not None else str(tid)),
                score=outcome.winning_score,
            )
            for tid in outcome.winner_ids
        )
        announcements.append(
            TournamentWinnerAnnouncement(
                tournament_id=tournament.id,
                name=tournament.name,
                n_entrants=repo.count_entries(tournament.id),
                pot_cents=outcome.pot_cents,
                prize_cents=outcome.prize_cents,
                winners=winners,
                per_winner_cents=outcome.per_winner_cents,
                remainder_cents=outcome.remainder_cents,
                is_correction=is_correction,
            )
        )

    return announcements
