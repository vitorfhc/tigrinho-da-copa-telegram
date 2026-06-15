"""CRUD repositories over the ORM models (COMPLETION.md §5, §6).

Each repository wraps a single :class:`~sqlalchemy.orm.Session`. Methods ``flush`` so generated
ids/defaults are populated and constraints fire eagerly, but **never commit** — the caller owns
the unit of work (the bot handler, job, or CLI command). The same repositories are shared by the
Telegram bot and the Typer admin CLI.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tigrinho.db.models import ApiUsage, Bet, Game, GameStatus, Player


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

    def list_upcoming(self, now: datetime) -> list[Game]:
        """Scheduled games whose kickoff is still in the future (open for bets), soonest first."""
        stmt = (
            select(Game)
            .where(Game.status == GameStatus.SCHEDULED, Game.kickoff_utc > now)
            .order_by(Game.kickoff_utc)
        )
        return list(self._session.execute(stmt).scalars())

    def list_active(self, now: datetime, window_hours: int) -> list[Game]:
        """Games inside their live window and not yet settled (for the poll job, §9.2)."""
        window_end_floor = now - timedelta(hours=window_hours)
        stmt = (
            select(Game)
            .where(
                Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE)),
                Game.kickoff_utc <= now,
                Game.kickoff_utc >= window_end_floor,
                Game.settled_at.is_(None),
            )
            .order_by(Game.kickoff_utc)
        )
        return list(self._session.execute(stmt).scalars())

    def list_unannounced(self, now: datetime) -> list[Game]:
        """Open games not yet announced to the group (announced or failed-to-announce; §9.1)."""
        stmt = (
            select(Game)
            .where(
                Game.status == GameStatus.SCHEDULED,
                Game.kickoff_utc > now,
                Game.announced_at.is_(None),
            )
            .order_by(Game.kickoff_utc)
        )
        return list(self._session.execute(stmt).scalars())

    def mark_announced(self, fixture_ids: list[int], when: datetime) -> None:
        """Record that these games were announced (so they are not re-announced; §9.1)."""
        for fixture_id in fixture_ids:
            game = self._session.get(Game, fixture_id)
            if game is not None:
                game.announced_at = when
        self._session.flush()

    def list_due_for_reminder(self, now: datetime, lead: timedelta) -> list[Game]:
        """Games of the soonest unreminded kickoff slot due for a ~1h reminder (§9.3).

        Selects announced, still-open, unreminded games inside their lead window
        (``now < kickoff_utc <= now + lead``), then narrows to those sharing the *soonest*
        such ``kickoff_utc`` — so only one kickoff slot is reminded per sweep and "combine"
        means exactly "same kickoff time".
        """
        stmt = (
            select(Game)
            .where(
                Game.status == GameStatus.SCHEDULED,
                Game.announced_at.is_not(None),
                Game.reminded_at.is_(None),
                Game.kickoff_utc > now,
                Game.kickoff_utc <= now + lead,
            )
            .order_by(Game.kickoff_utc)
        )
        due = list(self._session.execute(stmt).scalars())
        if not due:
            return []
        soonest = due[0].kickoff_utc
        return [game for game in due if game.kickoff_utc == soonest]

    def mark_reminded(self, fixture_ids: list[int], when: datetime) -> None:
        """Flag games reminded — only if still SCHEDULED and not already flagged (§9.3).

        The re-check skips a game that was voided (no longer SCHEDULED) or already flagged by
        another sweep between the read and this write, so it stays eligible for a later, real
        reminder. (A reschedule is handled separately: sync clears ``reminded_at`` — §9.1.)
        """
        for fixture_id in fixture_ids:
            game = self._session.get(Game, fixture_id)
            if (
                game is not None
                and game.status is GameStatus.SCHEDULED
                and game.reminded_at is None
            ):
                game.reminded_at = when
        self._session.flush()

    def list_stuck(self, now: datetime, window_hours: int) -> list[Game]:
        """Unsettled games past ``kickoff + window`` (need manual settlement; §9.2 safeguard)."""
        threshold = now - timedelta(hours=window_hours)
        stmt = (
            select(Game)
            .where(
                Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE)),
                Game.settled_at.is_(None),
                Game.kickoff_utc < threshold,
            )
            .order_by(Game.kickoff_utc)
        )
        return list(self._session.execute(stmt).scalars())

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

    def list_all(self) -> list[Bet]:
        return list(self._session.execute(select(Bet).order_by(Bet.id)).scalars())

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

    def list_settled(self) -> list[Bet]:
        """All graded bets (settled_at set) — the basis for the rebuildable scoreboard (§10)."""
        return list(self._session.execute(select(Bet).where(Bet.settled_at.is_not(None))).scalars())

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
