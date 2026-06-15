"""Tests for the CRUD repositories (COMPLETION.md §6, §16)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import Game, GameStatus, SquadPlayer, Stage
from tigrinho.db.repositories import (
    ApiUsageRepository,
    BetRepository,
    GameRepository,
    PlayerRepository,
    SquadRepository,
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


# --- SquadRepository ------------------------------------------------------------------------


def test_squad_upsert_many_and_list_sorted(session: Session) -> None:
    repo = SquadRepository(session)
    repo.upsert_many(
        [
            SquadPlayer(player_id=2, team_id=10, name="Vinícius"),
            SquadPlayer(player_id=1, team_id=10, name="Alisson"),
            SquadPlayer(player_id=3, team_id=20, name="Messi"),
        ]
    )
    team10 = repo.list_for_team(10)
    assert [p.name for p in team10] == ["Alisson", "Vinícius"]  # ordered by name
    assert repo.count_for_team(10) == 2
    assert repo.count_for_team(20) == 1
    assert repo.get(3) is not None


def test_squad_upsert_updates_existing(session: Session) -> None:
    repo = SquadRepository(session)
    repo.upsert_many([SquadPlayer(player_id=1, team_id=10, name="Old")])
    repo.upsert_many([SquadPlayer(player_id=1, team_id=10, name="New", position="GK")])
    player = repo.get(1)
    assert player is not None
    assert player.name == "New"
    assert player.position == "GK"
    assert repo.count_for_team(10) == 1


# --- ApiUsageRepository ---------------------------------------------------------------------


def test_api_usage_counter(session: Session) -> None:
    repo = ApiUsageRepository(session)
    today = date(2026, 6, 15)
    assert repo.get_count(today) == 0
    assert repo.increment(today) == 1
    assert repo.increment(today, by=4) == 5
    assert repo.get_count(today) == 5
    assert repo.get_count(date(2026, 6, 16)) == 0  # other days isolated
