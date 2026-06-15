"""CRUD repositories over the ORM models (COMPLETION.md §5, §6).

Each repository wraps a single :class:`~sqlalchemy.orm.Session`. Methods ``flush`` so generated
ids/defaults are populated and constraints fire eagerly, but **never commit** — the caller owns
the unit of work (the bot handler, job, or CLI command). The same repositories are shared by the
Telegram bot and the Typer admin CLI.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from tigrinho.db.models import ApiUsage, Bet, Game, Player, SquadPlayer


class PlayerRepository:
    """Players, keyed on ``telegram_id`` (auto-created on a user's first bet)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, telegram_id: int) -> Player | None:
        return self._session.get(Player, telegram_id)

    def get_or_create(self, telegram_id: int, display_name: str) -> Player:
        player = self._session.get(Player, telegram_id)
        if player is None:
            player = Player(telegram_id=telegram_id, display_name=display_name)
            self._session.add(player)
            self._session.flush()
        elif player.display_name != display_name:
            player.display_name = display_name
            self._session.flush()
        return player

    def list_all(self) -> list[Player]:
        return list(self._session.execute(select(Player)).scalars())

    def delete(self, telegram_id: int) -> bool:
        player = self._session.get(Player, telegram_id)
        if player is None:
            return False
        self._session.delete(player)
        self._session.flush()
        return True


class GameRepository:
    """Fixtures, keyed on the provider ``fixture_id``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, fixture_id: int) -> Game | None:
        return self._session.get(Game, fixture_id)

    def add(self, game: Game) -> Game:
        self._session.add(game)
        self._session.flush()
        return game

    def list_all(self) -> list[Game]:
        return list(self._session.execute(select(Game).order_by(Game.kickoff_utc)).scalars())

    def delete(self, fixture_id: int) -> bool:
        game = self._session.get(Game, fixture_id)
        if game is None:
            return False
        self._session.delete(game)
        self._session.flush()
        return True


class BetRepository:
    """Bets, enforcing one per (fixture, player, category) via upsert."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, bet_id: int) -> Bet | None:
        return self._session.get(Bet, bet_id)

    def get(self, fixture_id: int, player_telegram_id: int, category: str) -> Bet | None:
        stmt = select(Bet).where(
            Bet.fixture_id == fixture_id,
            Bet.player_telegram_id == player_telegram_id,
            Bet.category == category,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def upsert(
        self,
        *,
        fixture_id: int,
        player_telegram_id: int,
        category: str,
        payload_json: str,
    ) -> Bet:
        """Create the bet, or overwrite the existing one for this category (resetting grading)."""
        existing = self.get(fixture_id, player_telegram_id, category)
        if existing is not None:
            existing.payload_json = payload_json
            existing.is_correct = None
            existing.points_awarded = None
            existing.settled_at = None
            self._session.flush()
            return existing
        bet = Bet(
            fixture_id=fixture_id,
            player_telegram_id=player_telegram_id,
            category=category,
            payload_json=payload_json,
        )
        self._session.add(bet)
        self._session.flush()
        return bet

    def list_for_player(self, player_telegram_id: int) -> list[Bet]:
        stmt = select(Bet).where(Bet.player_telegram_id == player_telegram_id)
        return list(self._session.execute(stmt).scalars())

    def list_for_game(self, fixture_id: int) -> list[Bet]:
        stmt = select(Bet).where(Bet.fixture_id == fixture_id)
        return list(self._session.execute(stmt).scalars())

    def list_for_player_and_game(self, player_telegram_id: int, fixture_id: int) -> list[Bet]:
        stmt = select(Bet).where(
            Bet.player_telegram_id == player_telegram_id,
            Bet.fixture_id == fixture_id,
        )
        return list(self._session.execute(stmt).scalars())

    def delete(self, bet_id: int) -> bool:
        bet = self._session.get(Bet, bet_id)
        if bet is None:
            return False
        self._session.delete(bet)
        self._session.flush()
        return True


class SquadRepository:
    """Cached squad members (seeded/refreshed via the CLI; never fetched per game)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, player_id: int) -> SquadPlayer | None:
        return self._session.get(SquadPlayer, player_id)

    def list_for_team(self, team_id: int) -> list[SquadPlayer]:
        stmt = select(SquadPlayer).where(SquadPlayer.team_id == team_id).order_by(SquadPlayer.name)
        return list(self._session.execute(stmt).scalars())

    def count_for_team(self, team_id: int) -> int:
        return len(self.list_for_team(team_id))

    def upsert_many(self, players: Iterable[SquadPlayer]) -> None:
        for player in players:
            self._session.merge(player)
        self._session.flush()


class ApiUsageRepository:
    """Per-budget-day provider request counter (§7.3)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_count(self, budget_date: date) -> int:
        row = self._session.get(ApiUsage, budget_date)
        return row.count if row is not None else 0

    def increment(self, budget_date: date, by: int = 1) -> int:
        row = self._session.get(ApiUsage, budget_date)
        if row is None:
            row = ApiUsage(budget_date=budget_date, count=by)
            self._session.add(row)
        else:
            row.count += by
        self._session.flush()
        return row.count
