"""Tests for the DB settlement service (COMPLETION.md §8.3, §16)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.domain.bets import (
    ExactScorePayload,
    OverUnderPayload,
    OverUnderSel,
    WinnerPayload,
    WinnerSel,
    serialize_payload,
)
from tigrinho.providers.base import GoalEvent, MatchResult
from tigrinho.settlement_service import settle_fixture


def _seed(session: Session) -> Game:
    game = Game(
        fixture_id=1001,
        match_hash="h",
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
    PlayerRepository(session).get_or_create(42, "Alice")
    PlayerRepository(session).get_or_create(43, "Bob")
    bets = BetRepository(session)
    bets.upsert(
        fixture_id=1001,
        player_telegram_id=42,
        category="EXACT_SCORE",
        payload_json=serialize_payload(ExactScorePayload(home=2, away=1)),
    )
    bets.upsert(
        fixture_id=1001,
        player_telegram_id=42,
        category="WINNER",
        payload_json=serialize_payload(WinnerPayload(sel=WinnerSel.HOME)),
    )
    bets.upsert(
        fixture_id=1001,
        player_telegram_id=43,
        category="OVER_UNDER",
        payload_json=serialize_payload(OverUnderPayload(sel=OverUnderSel.UNDER)),
    )
    session.flush()
    return game


def _result() -> MatchResult:
    return MatchResult(
        fixture_id=1001,
        stage=Stage.GROUP,
        status=GameStatus.FINISHED,
        home_goals_90=2,
        away_goals_90=1,
        goals=(
            GoalEvent(
                minute=10,
                team_id=10,
                player_id=100,
                player_name="Neymar",
                is_own_goal=False,
                is_penalty=False,
            ),
        ),
        advancing_team_id=None,
        home_goals_ht=1,
        away_goals_ht=0,
    )


def test_settle_fixture_writes_grades_and_result(session: Session) -> None:
    game = _seed(session)
    summary = settle_fixture(session, game, _result())

    assert game.status is GameStatus.FINISHED
    assert game.home_goals_90 == 2
    assert game.home_goals_ht == 1  # half-time score persisted for re-settle
    assert game.away_goals_ht == 0
    assert game.settled_at is not None
    assert game.first_scorer_player_id == 100

    bets = {
        (b.player_telegram_id, b.category): b for b in BetRepository(session).list_for_game(1001)
    }
    assert bets[(42, "EXACT_SCORE")].points_awarded == 5  # 2-1 exact
    assert bets[(42, "WINNER")].points_awarded == 2  # home win
    assert bets[(43, "OVER_UNDER")].points_awarded == 0  # under, but total is 3

    # summary ordered by total points desc -> Alice (7) before Bob (0)
    assert [p.telegram_id for p in summary.players] == [42, 43]
    assert summary.players[0].total_points == 7
    assert summary.first_scorer_player_id == 100
    assert summary.first_scoring_team_name == "Brasil"  # team_id 10 scored first


def test_settle_fixture_is_idempotent(session: Session) -> None:
    game = _seed(session)
    first = settle_fixture(session, game, _result())
    second = settle_fixture(session, game, _result())
    assert first == second


def test_settle_fixture_reloaded_game(session: Session) -> None:
    game = _seed(session)
    settle_fixture(session, game, _result())
    reloaded = GameRepository(session).get(1001)
    assert reloaded is not None
    assert reloaded.status is GameStatus.FINISHED
    assert reloaded.away_goals_90 == 1
