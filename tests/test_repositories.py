"""Tests for the CRUD repositories (COMPLETION.md §6, §16)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import (
    ApiUsageRepository,
    BetRepository,
    GameRepository,
    PlayerRepository,
)


def _seed_game(session: Session, fixture_id: int = 1001) -> Game:
    game = Game(
        fixture_id=fixture_id,
        match_hash=f"hash-{fixture_id}",
        stage=Stage.GROUP,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=datetime(2026, 6, 16, 19, 0),
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        status=GameStatus.SCHEDULED,
    )
    session.add(game)
    session.flush()
    return game


# --- PlayerRepository -----------------------------------------------------------------------


def test_player_get_or_create_is_idempotent(session: Session) -> None:
    repo = PlayerRepository(session)
    first = repo.get_or_create(42, "Tigrão")
    second = repo.get_or_create(42, "Tigrão")
    assert first.telegram_id == second.telegram_id
    assert len(repo.list_all()) == 1


def test_player_get_or_create_updates_display_name(session: Session) -> None:
    repo = PlayerRepository(session)
    repo.get_or_create(42, "Old Name")
    updated = repo.get_or_create(42, "New Name")
    assert updated.display_name == "New Name"
    assert len(repo.list_all()) == 1


def test_player_delete(session: Session) -> None:
    repo = PlayerRepository(session)
    repo.get_or_create(42, "Tigrão")
    assert repo.delete(42) is True
    assert repo.delete(42) is False
    assert repo.get(42) is None


# --- GameRepository -------------------------------------------------------------------------


def test_game_add_get_list_delete(session: Session) -> None:
    repo = GameRepository(session)
    _seed_game(session, 1001)
    _seed_game(session, 1002)
    assert repo.get(1001) is not None
    assert len(repo.list_all()) == 2
    assert repo.delete(1001) is True
    assert repo.get(1001) is None
    assert repo.delete(9999) is False


def _game_at(fixture_id: int, kickoff: datetime, status: GameStatus = GameStatus.SCHEDULED) -> Game:
    return Game(
        fixture_id=fixture_id,
        match_hash=f"h{fixture_id}",
        stage=Stage.GROUP,
        home_team_id=1,
        home_team_name="A",
        away_team_id=2,
        away_team_name="B",
        kickoff_utc=kickoff,
        kickoff_local=kickoff,
        status=status,
    )


def test_game_list_upcoming(session: Session) -> None:
    repo = GameRepository(session)
    now = datetime(2026, 6, 16, 12, 0)
    session.add_all(
        [
            _game_at(1, datetime(2026, 6, 16, 19, 0)),  # future scheduled -> included
            _game_at(2, datetime(2026, 6, 16, 10, 0)),  # past -> excluded
            _game_at(3, datetime(2026, 6, 16, 20, 0), GameStatus.VOID),  # future but voided
        ]
    )
    session.flush()
    assert [g.fixture_id for g in repo.list_upcoming(now)] == [1]


def test_game_list_active(session: Session) -> None:
    repo = GameRepository(session)
    now = datetime(2026, 6, 16, 20, 0)
    session.add_all(
        [
            _game_at(1, datetime(2026, 6, 16, 19, 0)),  # 1h ago, within 3h window -> active
            _game_at(2, datetime(2026, 6, 16, 15, 0)),  # 5h ago, outside window
            _game_at(3, datetime(2026, 6, 16, 21, 0)),  # future, not started
        ]
    )
    session.flush()
    assert [g.fixture_id for g in repo.list_active(now, 3)] == [1]


# --- BetRepository --------------------------------------------------------------------------


def test_bet_upsert_creates_then_overwrites(session: Session) -> None:
    _seed_game(session)
    PlayerRepository(session).get_or_create(42, "Tigrão")
    repo = BetRepository(session)

    created = repo.upsert(
        fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel": "HOME"}'
    )
    created.is_correct = True
    created.points_awarded = 2
    session.flush()

    updated = repo.upsert(
        fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel": "AWAY"}'
    )
    assert updated.id == created.id  # no duplicate row
    assert updated.payload_json == '{"sel": "AWAY"}'
    assert updated.is_correct is None  # grading reset on re-bet
    assert updated.points_awarded is None
    assert len(repo.list_for_game(1001)) == 1


def test_bet_listing_helpers(session: Session) -> None:
    _seed_game(session, 1001)
    _seed_game(session, 1002)
    PlayerRepository(session).get_or_create(42, "A")
    PlayerRepository(session).get_or_create(43, "B")
    repo = BetRepository(session)
    repo.upsert(fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json="{}")
    repo.upsert(fixture_id=1001, player_telegram_id=42, category="BTTS", payload_json="{}")
    repo.upsert(fixture_id=1002, player_telegram_id=42, category="WINNER", payload_json="{}")
    repo.upsert(fixture_id=1001, player_telegram_id=43, category="WINNER", payload_json="{}")

    assert len(repo.list_for_player(42)) == 3
    assert len(repo.list_for_game(1001)) == 3
    assert len(repo.list_for_player_and_game(42, 1001)) == 2


def test_bet_delete(session: Session) -> None:
    _seed_game(session)
    PlayerRepository(session).get_or_create(42, "A")
    repo = BetRepository(session)
    bet = repo.upsert(fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json="{}")
    assert repo.delete(bet.id) is True
    assert repo.delete(bet.id) is False


# --- ApiUsageRepository ---------------------------------------------------------------------


def test_api_usage_counter(session: Session) -> None:
    repo = ApiUsageRepository(session)
    today = date(2026, 6, 15)
    assert repo.get_count(today) == 0
    assert repo.increment(today) == 1
    assert repo.increment(today, by=4) == 5
    assert repo.get_count(today) == 5
    assert repo.get_count(date(2026, 6, 16)) == 0  # other days isolated
