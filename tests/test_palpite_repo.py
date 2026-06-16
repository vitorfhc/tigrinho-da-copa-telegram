"""Tests for the AI-palpite persistence: model, repository, and the upcoming-window query."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from tigrinho.db.models import AiPalpite, Game, GameStatus, Stage
from tigrinho.db.repositories import GameRepository, PalpiteRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _add_game(session: Session, fixture_id: int, kickoff: datetime, status: GameStatus) -> None:
    session.add(
        Game(
            fixture_id=fixture_id,
            match_hash=f"h{fixture_id}",
            stage=Stage.GROUP,
            home_team_id=1,
            home_team_name="Brasil",
            away_team_id=2,
            away_team_name="Argentina",
            kickoff_utc=kickoff,
            kickoff_local=kickoff,
            status=status,
        )
    )
    session.flush()


def test_list_upcoming_within_window(session: Session) -> None:
    now = _now()
    _add_game(session, 1, now + timedelta(hours=2), GameStatus.SCHEDULED)  # in window
    _add_game(session, 2, now + timedelta(hours=30), GameStatus.SCHEDULED)  # beyond 24h
    _add_game(session, 3, now - timedelta(hours=1), GameStatus.SCHEDULED)  # already kicked off
    _add_game(session, 4, now + timedelta(hours=2), GameStatus.FINISHED)  # not scheduled

    games = GameRepository(session).list_upcoming_within(now, timedelta(hours=24))

    assert [g.fixture_id for g in games] == [1]


def test_palpite_upsert_creates_then_replaces(session: Session) -> None:
    now = _now()
    _add_game(session, 1, now + timedelta(hours=2), GameStatus.SCHEDULED)
    repo = PalpiteRepository(session)
    today = date(2026, 6, 16)

    created = repo.upsert(fixture_id=1, palpite_date=today, payload_json='{"v": 1}')
    assert created.payload_json == '{"v": 1}'

    updated = repo.upsert(fixture_id=1, palpite_date=today, payload_json='{"v": 2}')
    assert updated.id == created.id  # same row, not a duplicate
    assert updated.payload_json == '{"v": 2}'

    all_rows = session.query(AiPalpite).all()
    assert len(all_rows) == 1


def test_palpite_get_and_list_for_date(session: Session) -> None:
    now = _now()
    for fid in (1, 2, 3):
        _add_game(session, fid, now + timedelta(hours=2), GameStatus.SCHEDULED)
    repo = PalpiteRepository(session)
    today = date(2026, 6, 16)
    yesterday = date(2026, 6, 15)
    repo.upsert(fixture_id=1, palpite_date=today, payload_json="a")
    repo.upsert(fixture_id=2, palpite_date=today, payload_json="b")
    repo.upsert(fixture_id=3, palpite_date=yesterday, payload_json="c")  # other day

    assert repo.get(1, today) is not None
    assert repo.get(1, yesterday) is None

    rows = repo.list_for_date([1, 2, 3], today)
    assert {r.fixture_id for r in rows} == {1, 2}


def test_existing_fixture_ids(session: Session) -> None:
    now = _now()
    for fid in (1, 2):
        _add_game(session, fid, now + timedelta(hours=2), GameStatus.SCHEDULED)
    repo = PalpiteRepository(session)
    today = date(2026, 6, 16)
    repo.upsert(fixture_id=1, palpite_date=today, payload_json="a")

    assert repo.existing_fixture_ids([1, 2], today) == {1}
