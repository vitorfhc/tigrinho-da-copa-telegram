"""Tests for the CRUD repositories (COMPLETION.md §6, §16)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

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


def test_game_list_recently_ended(session: Session) -> None:
    repo = GameRepository(session)
    base = datetime(2026, 6, 16, 12, 0)
    finished_early = _game_at(1, datetime(2026, 6, 14, 19, 0), GameStatus.FINISHED)
    finished_early.settled_at = base
    finished_late = _game_at(2, datetime(2026, 6, 15, 19, 0), GameStatus.FINISHED)
    finished_late.settled_at = base + timedelta(hours=1)
    scheduled = _game_at(3, datetime(2026, 6, 17, 19, 0))  # not finished
    voided = _game_at(4, datetime(2026, 6, 13, 19, 0), GameStatus.VOID)
    voided.settled_at = base  # void games are excluded even with settled_at set
    session.add_all([finished_early, finished_late, scheduled, voided])
    session.flush()
    # Most recently settled first; only FINISHED games.
    assert [g.fixture_id for g in repo.list_recently_ended(15)] == [2, 1]
    assert [g.fixture_id for g in repo.list_recently_ended(1)] == [2]


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


# --- GameRepository.list_due_for_reminder / mark_reminded helper ----------------------------


def _reminder_game(
    fixture_id: int,
    now: datetime,
    minutes: int,
    *,
    status: GameStatus = GameStatus.SCHEDULED,
    announced: bool = True,
) -> Game:
    kickoff = now + timedelta(minutes=minutes)
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
        announced_at=now if announced else None,
    )


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


# --- GameRepository.list_due_for_reminder / mark_reminded -----------------------------------


def test_list_due_for_reminder_picks_soonest_slot(session: Session) -> None:
    now = datetime(2026, 6, 13, 18, 0)  # naive UTC
    repo = GameRepository(session)
    session.add_all(
        [
            _reminder_game(1, now, 30),  # soonest slot
            _reminder_game(2, now, 30),  # same slot as #1 -> combine
            _reminder_game(3, now, 40),  # later slot -> excluded this sweep
            _reminder_game(4, now, 30, announced=False),  # not announced -> excluded
        ]
    )
    session.flush()

    due = repo.list_due_for_reminder(now, timedelta(minutes=60))
    assert {g.fixture_id for g in due} == {1, 2}


def test_list_due_for_reminder_excludes_out_of_window_and_voided(session: Session) -> None:
    now = datetime(2026, 6, 13, 18, 0)
    repo = GameRepository(session)
    session.add_all(
        [
            _reminder_game(1, now, -5),  # already kicked off -> excluded
            _reminder_game(2, now, 90),  # beyond the 60-min lead -> excluded
            _reminder_game(3, now, 20, status=GameStatus.VOID),  # voided -> excluded
        ]
    )
    session.flush()

    assert repo.list_due_for_reminder(now, timedelta(minutes=60)) == []


def test_mark_reminded_only_flags_scheduled_unreminded(session: Session) -> None:
    now = datetime(2026, 6, 13, 18, 0)
    repo = GameRepository(session)
    # not SCHEDULED -> must NOT be flagged
    session.add(_reminder_game(1, now, 30, status=GameStatus.VOID))
    session.flush()

    repo.mark_reminded([1], now)
    game = repo.get(1)
    assert game is not None
    assert game.reminded_at is None  # re-validation skipped a non-SCHEDULED game
