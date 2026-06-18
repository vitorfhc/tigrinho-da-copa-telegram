"""Tests for tournament_service management/locking/auth (Feature 7 / §22)."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from tigrinho import tournament_service as svc
from tigrinho.db.models import Game, GameStatus, Stage, Tournament, TournamentStatus
from tigrinho.db.repositories import BetRepository, PlayerRepository, TournamentRepository

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


# --- resolution / announcements (§7) ---------------------------------------------------------


def _finish_game(session: Session, fixture_id: int) -> None:
    game = session.get(Game, fixture_id)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()


def _void_game(session: Session, fixture_id: int) -> None:
    game = session.get(Game, fixture_id)
    assert game is not None
    game.status = GameStatus.VOID
    session.flush()


def _graded_bet(session: Session, *, fixture_id: int, player: int, points: int) -> None:
    bet = BetRepository(session).upsert(
        fixture_id=fixture_id, player_telegram_id=player, category="WINNER", payload_json="{}"
    )
    bet.is_correct = points > 0
    bet.points_awarded = points
    bet.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()


def _running_tournament(session: Session, *, fixtures: list[int]) -> Tournament:
    for fid in fixtures:
        _game(session, fid)
    t = _draft(session)
    for fid in fixtures:
        svc.add_game(session, t, fid, now=_NOW)
    svc.open_tournament(session, t, now=_NOW)
    return t


def test_on_game_resolved_announces_single_winner_idempotently(session: Session) -> None:
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    t = _running_tournament(session, fixtures=[1, 2])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    svc.join(session, t, telegram_id=200, display_name="Bruno", now=_NOW)
    _graded_bet(session, fixture_id=1, player=100, points=5)
    _graded_bet(session, fixture_id=2, player=100, points=2)
    _graded_bet(session, fixture_id=1, player=200, points=2)
    _finish_game(session, 1)

    # Only game 1 done -> still running -> a partial placar is posted (§22.4).
    partial = svc.on_game_resolved(session, 1)
    assert len(partial) == 1
    p = partial[0]
    assert isinstance(p, svc.TournamentPartialAnnouncement)
    assert (p.settled_count, p.total_games, p.n_entrants) == (1, 2, 2)
    assert p.standings == (("Ana", 7), ("Bruno", 2))  # Ana leads
    # Re-resolving game 1 (no newly-finished game) is idempotent -> no second partial.
    assert svc.on_game_resolved(session, 1) == []
    _finish_game(session, 2)
    anns = svc.on_game_resolved(session, 2)
    assert len(anns) == 1
    ann = anns[0]
    assert isinstance(ann, svc.TournamentWinnerAnnouncement)
    assert ann.is_correction is False
    assert ann.n_entrants == 2
    assert ann.pot_cents == 2000
    assert ann.prize_cents == 1000
    assert [w.telegram_id for w in ann.winners] == [100]
    assert ann.winners[0].score == 7
    assert ann.per_winner_cents == 1000
    assert t.status is TournamentStatus.FINISHED
    # Re-running is a no-op (idempotent).
    assert svc.on_game_resolved(session, 2) == []


def test_on_game_resolved_last_game_void_still_finishes(session: Session) -> None:
    """F4: the last unresolved game becomes VOID (in sync, not settlement) — must still finish."""
    PlayerRepository(session).get_or_create(100, "Ana")
    t = _running_tournament(session, fixtures=[1, 2])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    _graded_bet(session, fixture_id=1, player=100, points=5)
    _finish_game(session, 1)
    # game 2 still scheduled -> a partial placar is posted, not the final result yet.
    partial = svc.on_game_resolved(session, 1)
    assert len(partial) == 1
    assert isinstance(partial[0], svc.TournamentPartialAnnouncement)
    _void_game(session, 2)
    anns = svc.on_game_resolved(session, 2)
    assert len(anns) == 1
    assert isinstance(anns[0], svc.TournamentWinnerAnnouncement)
    assert t.status is TournamentStatus.FINISHED


def test_partial_placar_posts_once_per_finished_game(session: Session) -> None:
    """§22.4: a partial placar each time a member game finishes, never twice for the same game,
    and never for the last game (the winner announcement covers it)."""
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    t = _running_tournament(session, fixtures=[1, 2, 3])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    svc.join(session, t, telegram_id=200, display_name="Bruno", now=_NOW)
    _graded_bet(session, fixture_id=1, player=100, points=5)
    _graded_bet(session, fixture_id=2, player=200, points=5)

    # Game 1 finishes -> partial #1.
    _finish_game(session, 1)
    first = svc.on_game_resolved(session, 1)
    assert len(first) == 1
    assert isinstance(first[0], svc.TournamentPartialAnnouncement)
    assert first[0].settled_count == 1
    assert t.partial_announced_count == 1

    # A void of another game is not a "finish" -> no partial, watermark unchanged.
    _void_game(session, 2)
    assert svc.on_game_resolved(session, 2) == []
    assert t.partial_announced_count == 1

    # Game 3 is the last unresolved game -> the final result fires, NOT a partial.
    _graded_bet(session, fixture_id=3, player=100, points=2)
    _finish_game(session, 3)
    last = svc.on_game_resolved(session, 3)
    assert len(last) == 1
    assert isinstance(last[0], svc.TournamentWinnerAnnouncement)
    assert t.status is TournamentStatus.FINISHED


def test_partial_placar_skipped_with_no_entrants(session: Session) -> None:
    """A still-running bolãozinho with no entrants has nothing to rank -> no partial post."""
    t = _running_tournament(session, fixtures=[1, 2])
    _finish_game(session, 1)
    assert svc.on_game_resolved(session, 1) == []
    assert t.partial_announced_count == 0


def test_on_game_resolved_zero_entrants_cancels(session: Session) -> None:
    t = _running_tournament(session, fixtures=[1])
    _finish_game(session, 1)
    anns = svc.on_game_resolved(session, 1)
    assert len(anns) == 1
    assert isinstance(anns[0], svc.TournamentNoResultAnnouncement)
    assert t.status is TournamentStatus.CANCELLED


def test_on_game_resolved_all_void_cancels(session: Session) -> None:
    PlayerRepository(session).get_or_create(100, "Ana")
    t = _running_tournament(session, fixtures=[1])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    _void_game(session, 1)
    anns = svc.on_game_resolved(session, 1)
    assert len(anns) == 1
    assert isinstance(anns[0], svc.TournamentNoResultAnnouncement)
    assert t.status is TournamentStatus.CANCELLED


def test_on_game_resolved_regrade_flips_winner_posts_correction(session: Session) -> None:
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    t = _running_tournament(session, fixtures=[1])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    svc.join(session, t, telegram_id=200, display_name="Bruno", now=_NOW)
    _graded_bet(session, fixture_id=1, player=100, points=5)
    _graded_bet(session, fixture_id=1, player=200, points=2)
    _finish_game(session, 1)
    first = svc.on_game_resolved(session, 1)
    assert [w.telegram_id for w in first[0].winners] == [100]  # type: ignore[union-attr]
    # A late re-grade flips the standings: Bruno now leads.
    _graded_bet(session, fixture_id=1, player=100, points=1)
    _graded_bet(session, fixture_id=1, player=200, points=5)
    anns = svc.on_game_resolved(session, 1)
    assert len(anns) == 1
    ann = anns[0]
    assert isinstance(ann, svc.TournamentWinnerAnnouncement)
    assert ann.is_correction is True
    assert [w.telegram_id for w in ann.winners] == [200]
    assert t.correction_count == 1


def test_on_game_resolved_revives_cancelled_after_unvoid(session: Session) -> None:
    """F5: an all-void CANCELLED bolãozinho whose game later plays must revive and announce."""
    PlayerRepository(session).get_or_create(100, "Ana")
    t = _running_tournament(session, fixtures=[1])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    _void_game(session, 1)
    assert isinstance(svc.on_game_resolved(session, 1)[0], svc.TournamentNoResultAnnouncement)
    assert TournamentRepository(session).get(t.id).status is TournamentStatus.CANCELLED  # type: ignore[union-attr]
    # The game is rescheduled, played, settled.
    _graded_bet(session, fixture_id=1, player=100, points=5)
    _finish_game(session, 1)
    anns = svc.on_game_resolved(session, 1)
    assert len(anns) == 1
    ann = anns[0]
    assert isinstance(ann, svc.TournamentWinnerAnnouncement)
    assert ann.is_correction is True
    assert TournamentRepository(session).get(t.id).status is TournamentStatus.FINISHED  # type: ignore[union-attr]


def test_on_game_resolved_skips_manually_cancelled(session: Session) -> None:
    PlayerRepository(session).get_or_create(100, "Ana")
    t = _running_tournament(session, fixtures=[1])
    svc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    _graded_bet(session, fixture_id=1, player=100, points=5)
    svc.cancel_tournament(session, t)  # manual cancel (no stored signature)
    _finish_game(session, 1)
    assert svc.on_game_resolved(session, 1) == []
    assert t.status is TournamentStatus.CANCELLED
