"""Tests for the ORM models, incl. the one-bet-per-category constraint (§6, §16)."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tigrinho.db.models import (
    Bet,
    Game,
    GameStatus,
    Player,
    SplitwiseMode,
    Stage,
    Tournament,
    TournamentStatus,
)


def _seed_player_and_game(session: Session) -> tuple[Player, Game]:
    player = Player(telegram_id=42, display_name="Tigrão")
    game = Game(
        fixture_id=1001,
        match_hash="hash-abc",
        stage=Stage.GROUP,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=datetime(2026, 6, 16, 19, 0),
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        status=GameStatus.SCHEDULED,
    )
    session.add_all([player, game])
    session.commit()
    return player, game


def test_create_and_query(session: Session) -> None:
    player, game = _seed_player_and_game(session)
    bet = Bet(
        fixture_id=game.fixture_id,
        player_telegram_id=player.telegram_id,
        category="WINNER",
        payload_json='{"sel": "HOME"}',
    )
    session.add(bet)
    session.commit()

    stored = session.execute(select(Bet)).scalar_one()
    assert stored.category == "WINNER"
    assert stored.created_at is not None
    assert stored.updated_at is not None
    assert stored.is_correct is None
    assert stored.points_awarded is None

    stored_game = session.get(Game, game.fixture_id)
    assert stored_game is not None
    assert stored_game.stage is Stage.GROUP
    assert stored_game.status is GameStatus.SCHEDULED
    assert stored_game.home_goals_90 is None
    assert stored_game.advancing_team_id is None


def test_splitwise_column_defaults(session: Session) -> None:
    player = Player(telegram_id=7, display_name="Tigrão")
    tournament = Tournament(
        name="Fase de Grupos",
        entry_price_cents=1000,
        status=TournamentStatus.DRAFT,
        created_by=7,
    )
    session.add_all([player, tournament])
    session.commit()

    stored_player = session.get(Player, 7)
    assert stored_player is not None
    assert stored_player.splitwise_user_id is None
    assert stored_player.splitwise_email is None

    stored = session.get(Tournament, tournament.id)
    assert stored is not None
    assert stored.splitwise_mode is SplitwiseMode.MANUAL
    assert stored.splitwise_expense_id is None
    assert stored.splitwise_synced_signature is None
    assert stored.splitwise_admin_notified_at is None


def test_one_bet_per_category_unique(session: Session) -> None:
    player, game = _seed_player_and_game(session)
    session.add(
        Bet(
            fixture_id=game.fixture_id,
            player_telegram_id=player.telegram_id,
            category="WINNER",
            payload_json='{"sel": "HOME"}',
        )
    )
    session.commit()

    session.add(
        Bet(
            fixture_id=game.fixture_id,
            player_telegram_id=player.telegram_id,
            category="WINNER",
            payload_json='{"sel": "AWAY"}',
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_different_category_same_game_allowed(session: Session) -> None:
    player, game = _seed_player_and_game(session)
    session.add_all(
        [
            Bet(
                fixture_id=game.fixture_id,
                player_telegram_id=player.telegram_id,
                category="WINNER",
                payload_json='{"sel": "HOME"}',
            ),
            Bet(
                fixture_id=game.fixture_id,
                player_telegram_id=player.telegram_id,
                category="OVER_UNDER",
                payload_json='{"sel": "OVER"}',
            ),
        ]
    )
    session.commit()
    assert len(session.execute(select(Bet)).scalars().all()) == 2


def test_relationship_cascade_delete(session: Session) -> None:
    player, game = _seed_player_and_game(session)
    session.add(
        Bet(
            fixture_id=game.fixture_id,
            player_telegram_id=player.telegram_id,
            category="WINNER",
            payload_json='{"sel": "HOME"}',
        )
    )
    session.commit()

    session.delete(game)
    session.commit()
    assert session.execute(select(Bet)).first() is None


def test_game_live_notification_defaults(session: Session) -> None:
    _player, game = _seed_player_and_game(session)
    stored = session.get(Game, game.fixture_id)
    assert stored is not None
    assert stored.started_at is None
    assert stored.goals_announced == 0
    assert stored.home_goals_announced == 0
    assert stored.away_goals_announced == 0


def test_game_last_reconciled_at_defaults_none_and_persists(session: Session) -> None:
    _player, game = _seed_player_and_game(session)
    stored = session.get(Game, game.fixture_id)
    assert stored is not None
    assert stored.last_reconciled_at is None
    stored.last_reconciled_at = datetime(2026, 6, 16, 21, 10)
    session.commit()
    assert session.get(Game, game.fixture_id).last_reconciled_at == datetime(2026, 6, 16, 21, 10)  # type: ignore[union-attr]
