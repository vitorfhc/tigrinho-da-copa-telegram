"""Tests for tournament_service management/locking/auth (Feature 7 / §22)."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from tigrinho import tournament_service as svc
from tigrinho.db.models import Game, GameStatus, Stage, Tournament, TournamentStatus
from tigrinho.db.repositories import TournamentRepository

_NOW = datetime(2026, 6, 16, 12, 0)


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


def _draft(session: Session, *, created_by: int = 1, price: int = 1000) -> Tournament:
    return svc.create_tournament(session, name="T", entry_price_cents=price, created_by=created_by)


def test_can_manage_creator_admin_only() -> None:
    t = Tournament(name="T", entry_price_cents=1000, status=TournamentStatus.DRAFT, created_by=7)
    assert svc.can_manage(t, 7, admin_id=999) is True
    assert svc.can_manage(t, 999, admin_id=999) is True
    assert svc.can_manage(t, 5, admin_id=999) is False


def test_require_manage_raises_for_others() -> None:
    t = Tournament(name="T", entry_price_cents=1000, status=TournamentStatus.DRAFT, created_by=7)
    with pytest.raises(svc.TournamentError):
        svc.require_manage(t, 5, admin_id=999)


def test_add_game_ok_and_rejects_started(session: Session) -> None:
    _game(session, 1, kickoff=datetime(2026, 6, 16, 19, 0))
    _game(session, 2, kickoff=datetime(2026, 6, 16, 10, 0))  # already kicked off
    t = _draft(session)
    svc.add_game(session, t, 1, now=_NOW)
    assert TournamentRepository(session).list_game_ids(t.id) == [1]
    with pytest.raises(svc.TournamentError):
        svc.add_game(session, t, 2, now=_NOW)  # started
    with pytest.raises(svc.TournamentError):
        svc.add_game(session, t, 999, now=_NOW)  # unknown


def test_set_price_rejected_after_entry(session: Session) -> None:
    _game(session, 1)
    t = _draft(session, price=1000)
    svc.add_game(session, t, 1, now=_NOW)
    svc.set_price(session, t, 2000, now=_NOW)  # ok, no entries yet
    assert t.entry_price_cents == 2000
    svc.open_tournament(session, t, now=_NOW)
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    with pytest.raises(svc.TournamentError):
        svc.set_price(session, t, 3000, now=_NOW)  # entries exist -> frozen


def test_open_preconditions(session: Session) -> None:
    t = _draft(session)
    with pytest.raises(svc.TournamentError):
        svc.open_tournament(session, t, now=_NOW)  # no games
    _game(session, 1, kickoff=datetime(2026, 6, 16, 10, 0))  # already started
    svc_repo = TournamentRepository(session)
    svc_repo.add_game(t.id, 1)
    with pytest.raises(svc.TournamentError):
        svc.open_tournament(session, t, now=_NOW)  # a game already started


def test_lock_freezes_games_and_joins_and_is_one_way(session: Session) -> None:
    game = _game(session, 1, kickoff=datetime(2026, 6, 16, 19, 0))
    t = _draft(session)
    svc.add_game(session, t, 1, now=_NOW)
    svc.open_tournament(session, t, now=_NOW)
    # Before kickoff: join works.
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    # After kickoff: lock engages — joins and edits are refused.
    after = datetime(2026, 6, 16, 19, 1)
    with pytest.raises(svc.TournamentError):
        svc.join(session, t, telegram_id=200, display_name="Bruno", now=after)
    assert t.locked_at is not None
    with pytest.raises(svc.TournamentError):
        svc.add_game(session, t, 1, now=after)
    # One-way: rescheduling the game far into the future does NOT clear the lock.
    game.kickoff_utc = datetime(2026, 6, 20, 19, 0)
    session.flush()
    svc.ensure_lock(session, t, datetime(2026, 6, 17, 12, 0))
    assert svc.is_locked(t) is True


def test_join_rejected_when_not_open(session: Session) -> None:
    _game(session, 1)
    t = _draft(session)
    with pytest.raises(svc.TournamentError):
        svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)


def test_join_already_is_friendly_noop(session: Session) -> None:
    _game(session, 1)
    t = _draft(session)
    svc.add_game(session, t, 1, now=_NOW)
    svc.open_tournament(session, t, now=_NOW)
    first = svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    second = svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    assert first.already is False
    assert second.already is True
    assert second.n_entrants == 1
    assert second.pot_cents == 1000
    assert second.prize_cents == 0  # lone entrant


def test_cancel_only_from_active(session: Session) -> None:
    t = _draft(session)
    svc.cancel_tournament(session, t)
    assert t.status is TournamentStatus.CANCELLED
    with pytest.raises(svc.TournamentError):
        svc.cancel_tournament(session, t)  # already terminal
