"""Repo tests for the daily-bolãozinho queries (§24)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tigrinho.db.models import Base, Game, GameStatus, Stage
from tigrinho.db.repositories import GameRepository, TournamentRepository


def _session_factory() -> sessionmaker:  # type: ignore[type-arg]  # noqa: PGH003
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _game(fid: int, kickoff: datetime, status: GameStatus = GameStatus.SCHEDULED) -> Game:
    return Game(
        fixture_id=fid,
        match_hash=f"h{fid}",
        stage=Stage.GROUP,
        home_team_id=fid * 10,
        home_team_name=f"Home{fid}",
        away_team_id=fid * 10 + 1,
        away_team_name=f"Away{fid}",
        kickoff_utc=kickoff,
        kickoff_local=kickoff,
        status=status,
    )


def test_list_scheduled_in_window_filters_by_status_and_bounds() -> None:
    sf = _session_factory()
    with sf() as s:
        s.add(_game(1, datetime(2026, 6, 21, 13, 0)))  # in window
        s.add(_game(2, datetime(2026, 6, 21, 23, 0)))  # in window
        s.add(_game(3, datetime(2026, 6, 22, 3, 0)))  # == end, excluded (half-open)
        s.add(_game(4, datetime(2026, 6, 20, 13, 0)))  # before window
        s.add(_game(5, datetime(2026, 6, 21, 16, 0), status=GameStatus.FINISHED))  # not scheduled
        s.commit()
    with sf() as s:
        games = GameRepository(s).list_scheduled_in_window(
            datetime(2026, 6, 21, 3, 0), datetime(2026, 6, 22, 3, 0)
        )
    assert [g.fixture_id for g in games] == [1, 2]


def test_daily_auto_for_finds_only_matching_date() -> None:
    sf = _session_factory()
    with sf() as s:
        repo = TournamentRepository(s)
        t = repo.create(name="Dia 21", entry_price_cents=1000, created_by=42)
        t.auto_created_for = date(2026, 6, 21)
        s.commit()
    with sf() as s:
        repo = TournamentRepository(s)
        assert repo.daily_auto_for(date(2026, 6, 21)) is not None
        assert repo.daily_auto_for(date(2026, 6, 22)) is None
