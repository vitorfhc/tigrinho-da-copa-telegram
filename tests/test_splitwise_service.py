"""Tests for the bolãozinho → Splitwise registration service (§23)."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

import tigrinho.tournament_service as tsvc
from tigrinho import splitwise_service as svc
from tigrinho.db.models import Game, GameStatus, SplitwiseMode, Stage, TournamentStatus
from tigrinho.db.repositories import BetRepository, PlayerRepository, TournamentRepository

_NOW = datetime(2026, 6, 16, 12, 0)


def _game(session: Session, fixture_id: int) -> None:
    session.add(
        Game(
            fixture_id=fixture_id,
            match_hash=f"h-{fixture_id}",
            stage=Stage.GROUP,
            home_team_id=10,
            home_team_name="Brasil",
            away_team_id=20,
            away_team_name="Argentina",
            kickoff_utc=datetime(2026, 6, 16, 19, 0),
            kickoff_local=datetime(2026, 6, 16, 19, 0),
            status=GameStatus.SCHEDULED,
        )
    )
    session.flush()


def _graded_bet(session: Session, *, fixture_id: int, player: int, points: int) -> None:
    bet = BetRepository(session).upsert(
        fixture_id=fixture_id, player_telegram_id=player, category="WINNER", payload_json="{}"
    )
    bet.is_correct = points > 0
    bet.points_awarded = points
    bet.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()


def _finish(session: Session, fixture_id: int) -> None:
    game = session.get(Game, fixture_id)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()


def _link(session: Session, telegram_id: int, user_id: int) -> None:
    player = PlayerRepository(session).get(telegram_id)
    assert player is not None
    player.splitwise_user_id = user_id
    player.splitwise_email = f"{telegram_id}@x.com"
    session.flush()


def _finished_tournament(
    session: Session, *, ana_pts: int, bruno_pts: int, price: int = 1000, link: bool = True
) -> int:
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    for fid in (1, 2):
        _game(session, fid)
    t = tsvc.create_tournament(session, name="Fase", entry_price_cents=price, created_by=1)
    for fid in (1, 2):
        tsvc.add_game(session, t, fid, now=_NOW)
    tsvc.open_tournament(session, t, now=_NOW)
    tsvc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    tsvc.join(session, t, telegram_id=200, display_name="Bruno", now=_NOW)
    _graded_bet(session, fixture_id=1, player=100, points=ana_pts)
    _graded_bet(session, fixture_id=1, player=200, points=bruno_pts)
    _finish(session, 1)
    _finish(session, 2)
    if link:
        _link(session, 100, 1001)
        _link(session, 200, 1002)
    return t.id


def test_initial_splitwise_mode() -> None:
    assert svc.initial_splitwise_mode(TournamentStatus.FINISHED) is SplitwiseMode.EXCLUDED
    assert svc.initial_splitwise_mode(TournamentStatus.CANCELLED) is SplitwiseMode.EXCLUDED
    assert svc.initial_splitwise_mode(TournamentStatus.OPEN) is SplitwiseMode.MANUAL
    assert svc.initial_splitwise_mode(TournamentStatus.DRAFT) is SplitwiseMode.MANUAL


def test_build_registration_single_winner(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2)
    reg = svc.build_registration(session, tid)
    assert reg is not None
    assert reg.expense_id is None and reg.is_correction is False
    assert reg.cost_cents == 1000  # one loser owes the entry
    by_user = {s.user_id: s for s in reg.shares}
    assert by_user[1001].paid_cents == 1000 and by_user[1001].owed_cents == 0  # Ana (winner)
    assert by_user[1002].paid_cents == 0 and by_user[1002].owed_cents == 1000  # Bruno (loser)
    assert "Ana" in reg.description


def test_build_registration_none_when_unlinked(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2, link=False)
    assert svc.build_registration(session, tid) is None


def test_build_registration_none_when_excluded(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2)
    t = TournamentRepository(session).get(tid)
    assert t is not None
    t.splitwise_mode = SplitwiseMode.EXCLUDED
    session.flush()
    assert svc.build_registration(session, tid) is None


def test_build_registration_none_when_already_synced(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2)
    reg = svc.build_registration(session, tid)
    assert reg is not None
    t = TournamentRepository(session).get(tid)
    assert t is not None
    t.splitwise_synced_signature = reg.signature
    session.flush()
    assert svc.build_registration(session, tid) is None


def test_build_registration_full_tie_is_zero_cost_none(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=5)  # tie → no losers → cost 0
    assert svc.build_registration(session, tid) is None


def test_build_registration_correction_when_expense_exists(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2)
    t = TournamentRepository(session).get(tid)
    assert t is not None
    t.splitwise_expense_id = 555
    session.flush()
    reg = svc.build_registration(session, tid)
    assert reg is not None
    assert reg.expense_id == 555 and reg.is_correction is True


def test_all_entrants_linked(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2, link=False)
    assert svc.all_entrants_linked(session, tid) is False
    _link(session, 100, 1001)
    _link(session, 200, 1002)
    assert svc.all_entrants_linked(session, tid) is True


def test_mark_synced(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2)
    t = TournamentRepository(session).get(tid)
    assert t is not None
    svc.mark_synced(t, expense_id=999, signature="sig-1")
    assert t.splitwise_expense_id == 999
    assert t.splitwise_synced_signature == "sig-1"


def test_manual_ready_and_registerable_and_auto_filters(session: Session) -> None:
    tid = _finished_tournament(session, ana_pts=5, bruno_pts=2)
    t = TournamentRepository(session).get(tid)
    assert t is not None
    t.status = TournamentStatus.FINISHED
    t.splitwise_mode = SplitwiseMode.MANUAL
    session.flush()

    ready = svc.manual_ready_to_notify(session)
    assert [x.id for x in ready] == [tid]
    assert [x.id for x in svc.manual_registerable(session)] == [tid]
    assert svc.finished_auto_tournaments(session) == []  # it's MANUAL, not AUTO

    # Once notified, it is no longer "ready to notify" but is still registerable.
    t.splitwise_admin_notified_at = _NOW
    session.flush()
    assert svc.manual_ready_to_notify(session) == []
    assert [x.id for x in svc.manual_registerable(session)] == [tid]

    # AUTO finished tournaments are listed for sweep retry.
    t.splitwise_mode = SplitwiseMode.AUTO
    session.flush()
    assert [x.id for x in svc.finished_auto_tournaments(session)] == [tid]


def test_open_tournament_stamps_auto_only_when_enabled(session: Session) -> None:
    for fid in (1, 2):
        _game(session, fid)
    t = tsvc.create_tournament(session, name="X", entry_price_cents=1000, created_by=1)
    tsvc.add_game(session, t, 1, now=_NOW)
    tsvc.open_tournament(session, t, now=_NOW, splitwise_enabled=True)
    assert t.splitwise_mode is SplitwiseMode.AUTO


def test_join_guard_requires_link_only_for_auto(session: Session) -> None:
    _game(session, 1)
    t = tsvc.create_tournament(session, name="X", entry_price_cents=1000, created_by=1)
    tsvc.add_game(session, t, 1, now=_NOW)
    tsvc.open_tournament(session, t, now=_NOW, splitwise_enabled=True)  # AUTO
    # Unlinked join into an AUTO bolãozinho is rejected.
    with pytest.raises(tsvc.TournamentError):
        tsvc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    # After linking, the join succeeds.
    PlayerRepository(session).get_or_create(100, "Ana")
    _link(session, 100, 1001)
    result = tsvc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    assert result.n_entrants == 1


def test_join_no_guard_for_manual(session: Session) -> None:
    _game(session, 1)
    t = tsvc.create_tournament(session, name="X", entry_price_cents=1000, created_by=1)
    tsvc.add_game(session, t, 1, now=_NOW)
    tsvc.open_tournament(session, t, now=_NOW)  # MANUAL (feature off)
    result = tsvc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    assert result.n_entrants == 1  # unlinked join is fine for MANUAL
