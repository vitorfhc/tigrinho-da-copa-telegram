"""Tests for the combined-board record loader (COMPLETION.md §10)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.board_data import load_games_records
from tigrinho.db.models import Game, GameStatus, Stage, utcnow
from tigrinho.db.repositories import BetRepository, PlayerRepository


def _seed(
    session: Session,
    *,
    fixture_id: int,
    status: GameStatus,
    telegram_id: int,
    name: str,
    points: int,
) -> None:
    kickoff = datetime(2026, 6, 16, 12, 0)
    session.add(
        Game(
            fixture_id=fixture_id,
            match_hash=f"h{fixture_id}",
            stage=Stage.GROUP,
            home_team_id=10,
            home_team_name="A",
            away_team_id=20,
            away_team_name="B",
            kickoff_utc=kickoff,
            kickoff_local=kickoff,
            status=status,
            home_goals_90=1,
            away_goals_90=0,
            settled_at=utcnow(),
        )
    )
    PlayerRepository(session).get_or_create(telegram_id, name)
    bet = BetRepository(session).upsert(
        fixture_id=fixture_id, player_telegram_id=telegram_id, category="WINNER", payload_json="{}"
    )
    bet.points_awarded = points
    bet.is_correct = points > 0
    bet.settled_at = utcnow()
    session.flush()


def test_load_games_records_sums_player_across_games(session: Session) -> None:
    _seed(session, fixture_id=1, status=GameStatus.FINISHED, telegram_id=42, name="Ana", points=5)
    _seed(session, fixture_id=2, status=GameStatus.FINISHED, telegram_id=42, name="Ana", points=2)
    records = load_games_records(session, [1, 2])
    assert len(records) == 2
    assert sum(r.points for r in records if r.telegram_id == 42) == 7


def test_load_games_records_excludes_voided_game(session: Session) -> None:
    _seed(session, fixture_id=1, status=GameStatus.FINISHED, telegram_id=42, name="Ana", points=5)
    _seed(session, fixture_id=2, status=GameStatus.VOID, telegram_id=42, name="Ana", points=2)
    records = load_games_records(session, [1, 2])
    assert len(records) == 1
    assert records[0].points == 5
