"""Tests for TournamentRepository (Feature 7 / §22)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import Game, GameStatus, Stage, TournamentStatus
from tigrinho.db.repositories import BetRepository, PlayerRepository, TournamentRepository


def _game(
    session: Session,
    fixture_id: int,
    *,
    kickoff: datetime = datetime(2026, 6, 16, 19, 0),
    status: GameStatus = GameStatus.SCHEDULED,
) -> Game:
    game = Game(
        fixture_id=fixture_id,
        match_hash=f"hash-{fixture_id}",
        stage=Stage.GROUP,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=kickoff,
        kickoff_local=kickoff,
        status=status,
    )
    session.add(game)
    session.flush()
    return game


def _graded_bet(
    session: Session, *, fixture_id: int, player: int, points: int, settled: bool = True
) -> None:
    bet = BetRepository(session).upsert(
        fixture_id=fixture_id, player_telegram_id=player, category="WINNER", payload_json="{}"
    )
    if settled:
        bet.is_correct = points > 0
        bet.points_awarded = points
        bet.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()


def test_create_get_list(session: Session) -> None:
    repo = TournamentRepository(session)
    t = repo.create(name="Oitavas", entry_price_cents=1000, created_by=7)
    assert repo.get(t.id) is not None
    assert repo.get(t.id).name == "Oitavas"  # type: ignore[union-attr]
    assert len(repo.list_all()) == 1
    assert repo.delete(t.id) is True
    assert repo.delete(t.id) is False


def test_add_game_is_idempotent_and_removable(session: Session) -> None:
    repo = TournamentRepository(session)
    _game(session, 1)
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    repo.add_game(t.id, 1)
    repo.add_game(t.id, 1)  # idempotent
    assert repo.list_game_ids(t.id) == [1]
    assert repo.remove_game(t.id, 1) is True
    assert repo.remove_game(t.id, 1) is False
    assert repo.list_game_ids(t.id) == []


def test_game_can_belong_to_many_tournaments(session: Session) -> None:
    repo = TournamentRepository(session)
    _game(session, 1)
    a = repo.create(name="A", entry_price_cents=1000, created_by=1)
    b = repo.create(name="B", entry_price_cents=2000, created_by=2)
    repo.add_game(a.id, 1)
    repo.add_game(b.id, 1)
    ids = {t.id for t in repo.tournaments_for_game(1)}
    assert ids == {a.id, b.id}


def test_entries_unique_and_counted(session: Session) -> None:
    repo = TournamentRepository(session)
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    assert repo.add_entry(t.id, 100) is True
    assert repo.add_entry(t.id, 100) is False  # already entered
    assert repo.add_entry(t.id, 200) is True
    assert repo.count_entries(t.id) == 2
    assert set(repo.entry_ids(t.id)) == {100, 200}
    assert repo.is_entered(t.id, 100) is True
    assert repo.is_entered(t.id, 999) is False
    assert repo.remove_entry(t.id, 100) is True
    assert repo.count_entries(t.id) == 1


def test_earliest_kickoff(session: Session) -> None:
    repo = TournamentRepository(session)
    _game(session, 1, kickoff=datetime(2026, 6, 16, 19, 0))
    _game(session, 2, kickoff=datetime(2026, 6, 16, 16, 0))
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    assert repo.earliest_kickoff(t.id) is None
    repo.add_game(t.id, 1)
    repo.add_game(t.id, 2)
    assert repo.earliest_kickoff(t.id) == datetime(2026, 6, 16, 16, 0)


def test_all_games_resolved(session: Session) -> None:
    repo = TournamentRepository(session)
    _game(session, 1, status=GameStatus.FINISHED)
    _game(session, 2, status=GameStatus.SCHEDULED)
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    assert repo.all_games_resolved(t.id) is False  # no games yet
    repo.add_game(t.id, 1)
    repo.add_game(t.id, 2)
    assert repo.all_games_resolved(t.id) is False  # game 2 still scheduled
    session.get(Game, 2).status = GameStatus.VOID  # type: ignore[union-attr]
    session.flush()
    assert repo.all_games_resolved(t.id) is True  # finished + void => resolved


def test_has_only_void_games(session: Session) -> None:
    repo = TournamentRepository(session)
    _game(session, 1, status=GameStatus.VOID)
    _game(session, 2, status=GameStatus.VOID)
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    assert repo.has_only_void_games(t.id) is False  # no games
    repo.add_game(t.id, 1)
    repo.add_game(t.id, 2)
    assert repo.has_only_void_games(t.id) is True
    session.get(Game, 2).status = GameStatus.FINISHED  # type: ignore[union-attr]
    session.flush()
    assert repo.has_only_void_games(t.id) is False


def test_standings_only_entrants_graded_nonvoid(session: Session) -> None:
    repo = TournamentRepository(session)
    for tid, name in [(100, "Ana"), (200, "Bruno"), (300, "Caio"), (400, "Dudu")]:
        PlayerRepository(session).get_or_create(tid, name)
    _game(session, 1, status=GameStatus.FINISHED)
    _game(session, 2, status=GameStatus.FINISHED)
    _game(session, 3, status=GameStatus.VOID)  # excluded from scoring
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    for fid in (1, 2, 3):
        repo.add_game(t.id, fid)
    repo.add_entry(t.id, 100)  # Ana: bets on 1 (5) + 2 (2) = 7
    repo.add_entry(t.id, 200)  # Bruno: bet only on void game 3 => 0
    repo.add_entry(t.id, 300)  # Caio: no bets => 0
    # Dudu (400) is NOT an entrant but bets on game 1 — must be excluded.
    _graded_bet(session, fixture_id=1, player=100, points=5)
    _graded_bet(session, fixture_id=2, player=100, points=2)
    _graded_bet(session, fixture_id=3, player=200, points=5)  # void game -> not counted
    _graded_bet(session, fixture_id=1, player=400, points=5)  # non-entrant -> excluded

    standings = repo.standings(t.id)
    assert standings == {100: 7, 200: 0, 300: 0}


def test_standings_empty_when_no_entrants(session: Session) -> None:
    repo = TournamentRepository(session)
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    assert repo.standings(t.id) == {}


def test_standings_ignores_ungraded_bets(session: Session) -> None:
    repo = TournamentRepository(session)
    PlayerRepository(session).get_or_create(100, "Ana")
    _game(session, 1, status=GameStatus.SCHEDULED)
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    repo.add_game(t.id, 1)
    repo.add_entry(t.id, 100)
    _graded_bet(session, fixture_id=1, player=100, points=0, settled=False)  # ungraded
    assert repo.standings(t.id) == {100: 0}


def test_reconcilable_member_games_only_active_tournaments(session: Session) -> None:
    repo = TournamentRepository(session)
    g1 = _game(session, 1, status=GameStatus.FINISHED)
    g1.settled_at = datetime(2026, 6, 16, 21, 0)
    g2 = _game(session, 2, status=GameStatus.FINISHED)
    g2.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()
    active = repo.create(name="Active", entry_price_cents=1000, created_by=1)
    repo.add_game(active.id, 1)
    finished_t = repo.create(name="Done", entry_price_cents=1000, created_by=1)
    repo.add_game(finished_t.id, 2)
    finished_t.status = TournamentStatus.FINISHED
    session.flush()
    ids = {g.fixture_id for g in repo.reconcilable_member_games()}
    assert ids == {1}


def test_count_settled_games(session: Session) -> None:
    repo = TournamentRepository(session)
    g1 = _game(session, 1, status=GameStatus.FINISHED)
    g1.settled_at = datetime(2026, 6, 16, 21, 0)
    _game(session, 2, status=GameStatus.VOID)
    g3 = _game(session, 3, status=GameStatus.FINISHED)  # finished but not yet settled
    session.flush()
    t = repo.create(name="T", entry_price_cents=1000, created_by=1)
    for fid in (1, 2, 3):
        repo.add_game(t.id, fid)
    # Only the FINISHED-and-settled game counts (void and unsettled-finished are excluded).
    assert repo.count_settled_games(t.id) == 1
    g3.settled_at = datetime(2026, 6, 16, 22, 0)
    session.flush()
    assert repo.count_settled_games(t.id) == 2
    # An empty bolãozinho has zero.
    empty = repo.create(name="E", entry_price_cents=1, created_by=1)
    assert repo.count_settled_games(empty.id) == 0


def test_list_with_standings(session: Session) -> None:
    repo = TournamentRepository(session)
    g1 = _game(session, 1, status=GameStatus.FINISHED)
    g1.settled_at = datetime(2026, 6, 16, 21, 0)
    g2 = _game(session, 2, status=GameStatus.FINISHED)
    g2.settled_at = datetime(2026, 6, 16, 21, 0)
    _game(session, 3, status=GameStatus.SCHEDULED)
    session.flush()

    finished = repo.create(name="Fin", entry_price_cents=1000, created_by=1)
    repo.add_game(finished.id, 1)
    finished.status = TournamentStatus.FINISHED

    open_with = repo.create(name="OpenWith", entry_price_cents=1000, created_by=1)
    repo.add_game(open_with.id, 2)
    open_with.status = TournamentStatus.OPEN

    open_without = repo.create(name="OpenWithout", entry_price_cents=1000, created_by=1)
    repo.add_game(open_without.id, 3)  # only a SCHEDULED game -> nothing to show
    open_without.status = TournamentStatus.OPEN

    repo.create(name="Draft", entry_price_cents=1000, created_by=1)  # DRAFT -> excluded
    session.flush()

    # FINISHED + OPEN-with-≥1-settled-game; DRAFT and not-yet-started OPEN are excluded.
    assert {t.id for t in repo.list_with_standings()} == {finished.id, open_with.id}
