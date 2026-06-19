"""Tests for the Typer admin CLI (COMPLETION.md §13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session, sessionmaker
from telegram import User
from typer.testing import CliRunner

import tigrinho.cli as cli
from tigrinho.cli import CliContext, app
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, Stage, utcnow
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.providers.base import Fixture
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider

runner = CliRunner()


def _patch_context(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session_factory: sessionmaker[Session],
    provider: FakeProvider | None = None,
) -> CliContext:
    budget = RequestBudget(
        session_factory, daily_cap=settings.api_daily_cap, reset_tz=settings.budget_tzinfo
    )
    ctx = CliContext(
        settings=settings,
        session_factory=session_factory,
        provider=provider or FakeProvider(),
        budget=budget,
    )
    monkeypatch.setattr(cli, "build_cli_context", lambda: ctx)
    return ctx


def _seed_game(
    session_factory: sessionmaker[Session], *, status: GameStatus = GameStatus.SCHEDULED
) -> None:
    kickoff = datetime(2026, 6, 16, 19, 0)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=1001,
                match_hash="h",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=status,
            )
        )
        session.commit()


def test_games_list(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    _seed_game(session_factory)
    result = runner.invoke(app, ["games", "list"])
    assert result.exit_code == 0
    assert "Brasil x Argentina" in result.stdout


def test_players_list(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    with session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Alice")
        session.commit()
    result = runner.invoke(app, ["players", "list"])
    assert result.exit_code == 0
    assert "Alice" in result.stdout


def test_games_delete_requires_yes(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    _seed_game(session_factory)
    refused = runner.invoke(app, ["games", "delete", "1001"])
    assert refused.exit_code == 1
    assert "Refusing" in refused.stdout
    ok = runner.invoke(app, ["games", "delete", "1001", "--yes"])
    assert ok.exit_code == 0
    with session_factory() as session:
        assert GameRepository(session).get(1001) is None


def test_set_result_settles(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    _seed_game(session_factory)
    with session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Alice")
        BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        session.commit()

    result = runner.invoke(app, ["set-result", "1001", "2", "1"])
    assert result.exit_code == 0
    assert "Settled" in result.stdout
    with session_factory() as session:
        game = GameRepository(session).get(1001)
        assert game is not None and game.status is GameStatus.FINISHED
        bet = BetRepository(session).list_for_game(1001)[0]
        assert bet.points_awarded == 2  # home win


def test_set_result_re_grades_on_correction(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    _seed_game(session_factory)
    with session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Alice")
        BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json='{"sel":"HOME"}'
        )
        session.commit()

    # First settle: 2-1 home win → WINNER:HOME correct, 2 pts.
    assert runner.invoke(app, ["set-result", "1001", "2", "1"]).exit_code == 0
    with session_factory() as session:
        assert BetRepository(session).list_for_game(1001)[0].points_awarded == 2

    # Correction: 0-1 away win → the same bet must be re-graded to incorrect, 0 pts.
    assert runner.invoke(app, ["set-result", "1001", "0", "1"]).exit_code == 0
    with session_factory() as session:
        bet = BetRepository(session).list_for_game(1001)[0]
        assert bet.points_awarded == 0
        assert bet.is_correct is False


def test_set_result_not_found(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    result = runner.invoke(app, ["set-result", "9999", "1", "0"])
    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_budget(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    result = runner.invoke(app, ["budget"])
    assert result.exit_code == 0
    assert "Budget" in result.stdout


def test_board(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    with session_factory() as session:
        kickoff = datetime.now(settings.tzinfo).replace(tzinfo=None)
        session.add(
            Game(
                fixture_id=1001,
                match_hash="h",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.FINISHED,
                settled_at=utcnow(),
            )
        )
        PlayerRepository(session).get_or_create(42, "Alice")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=42, category="WINNER", payload_json="{}"
        )
        bet.points_awarded = 2
        bet.is_correct = True
        bet.settled_at = utcnow()
        session.commit()
    result = runner.invoke(app, ["board"])
    assert result.exit_code == 0
    assert "Alice" in result.stdout


def test_db_dump(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    _seed_game(session_factory)
    result = runner.invoke(app, ["db", "--table", "games"])
    assert result.exit_code == 0
    assert "Brasil" in result.stdout


def test_sync(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    fixture = Fixture(
        fixture_id=2002,
        stage=Stage.GROUP,
        home_team_id=10,
        home_team_name="Brasil",
        away_team_id=20,
        away_team_name="Argentina",
        kickoff_utc=datetime.now(tz=UTC) + timedelta(hours=5),
        status=GameStatus.SCHEDULED,
    )
    _patch_context(monkeypatch, settings, session_factory, FakeProvider(fixtures=[fixture]))
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "new=1" in result.stdout


def test_telegram_info(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    monkeypatch.setattr(
        cli,
        "_get_me",
        AsyncMock(
            return_value=User(id=7, is_bot=True, first_name="Bot", username="TigrinhoDaCopaBot")
        ),
    )
    result = runner.invoke(app, ["telegram-info"])
    assert result.exit_code == 0
    assert "@TigrinhoDaCopaBot" in result.stdout
    assert str(settings.group_chat_id) in result.stdout


def _seed_future_game(session_factory: sessionmaker[Session], fixture_id: int) -> None:
    kickoff = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(hours=3)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
            )
        )
        session.commit()


def test_bolaozinho_cli_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    created = runner.invoke(app, ["bolaozinho", "create", "Oitavas", "--price", "10"])
    assert created.exit_code == 0
    assert "Created bolãozinho #1" in created.stdout

    listed = runner.invoke(app, ["bolaozinho", "list"])
    assert "Oitavas" in listed.stdout

    _seed_future_game(session_factory, 2001)
    added = runner.invoke(app, ["bolaozinho", "add-game", "1", "2001"])
    assert added.exit_code == 0
    assert "Added." in added.stdout

    runner.invoke(app, ["bolaozinho", "add-entry", "1", "555"])
    entries = runner.invoke(app, ["bolaozinho", "entries", "1"])
    assert "555" in entries.stdout

    # cancel needs --yes
    assert runner.invoke(app, ["bolaozinho", "cancel", "1"]).exit_code != 0
    cancelled = runner.invoke(app, ["bolaozinho", "cancel", "1", "--yes"])
    assert cancelled.exit_code == 0
    assert "Cancelled." in cancelled.stdout


def test_bolaozinho_add_entry_preserves_existing_name(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    """Admin add-entry must not clobber an existing player's display name."""
    _patch_context(monkeypatch, settings, session_factory)
    with session_factory() as session:
        PlayerRepository(session).get_or_create(555, "Caio Habibe")
        session.commit()

    assert runner.invoke(app, ["bolaozinho", "create", "Oitavas", "--price", "10"]).exit_code == 0
    added = runner.invoke(app, ["bolaozinho", "add-entry", "1", "555"])
    assert added.exit_code == 0
    assert "Added." in added.stdout

    with session_factory() as session:
        player = PlayerRepository(session).get(555)
        assert player is not None
        assert player.display_name == "Caio Habibe"


def test_bolaozinho_cli_bad_price(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    _patch_context(monkeypatch, settings, session_factory)
    result = runner.invoke(app, ["bolaozinho", "create", "X", "--price", "abc"])
    assert result.exit_code == 1
    assert "Invalid price" in result.stdout
