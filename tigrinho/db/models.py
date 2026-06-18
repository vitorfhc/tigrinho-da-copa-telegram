"""SQLAlchemy 2.0 typed ORM models (COMPLETION.md §6).

Grounding (per §2), verified June 2026:
- SQLAlchemy 2.0 — https://docs.sqlalchemy.org/en/20/orm/quickstart.html
  Modern declarative mapping: ``DeclarativeBase`` + ``Mapped[...]`` + ``mapped_column(...)``;
  nullability follows ``Mapped[X | None]``; PEP 484 typing is native (no mypy plugin needed).

Timestamp convention: SQLite has no timezone type, so **every stored datetime is naive UTC**
(see :func:`utcnow`). Local-time display values (``kickoff_local``) are stored naive in the
configured ``timezone``. The bot compares ``now`` against ``kickoff_utc`` using naive UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Re-exported so existing imports (`from tigrinho.db.models import Stage, GameStatus`) keep working;
# the canonical home is the dependency-free leaf module ``tigrinho.enums``.
from tigrinho.enums import GameStatus, Stage, TournamentStatus

__all__ = [
    "AiPalpite",
    "ApiUsage",
    "Base",
    "Bet",
    "Game",
    "GameStatus",
    "Player",
    "Stage",
    "Tournament",
    "TournamentEntry",
    "TournamentGame",
    "TournamentStatus",
    "utcnow",
]


def utcnow() -> datetime:
    """Current time as a naive UTC datetime (the project-wide storage convention)."""
    return datetime.now(tz=UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Player(Base):
    """A Telegram user who has placed at least one bet (auto-created on first bet)."""

    __tablename__ = "players"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    display_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    bets: Mapped[list[Bet]] = relationship(back_populates="player", cascade="all, delete-orphan")


class Game(Base):
    """A World Cup fixture, keyed on the provider's canonical ``fixture_id``."""

    __tablename__ = "games"

    fixture_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    match_hash: Mapped[str] = mapped_column(String)
    stage: Mapped[Stage] = mapped_column(Enum(Stage, name="stage"))
    home_team_id: Mapped[int] = mapped_column(Integer)
    home_team_name: Mapped[str] = mapped_column(String)
    away_team_id: Mapped[int] = mapped_column(Integer)
    away_team_name: Mapped[str] = mapped_column(String)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime)
    kickoff_local: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[GameStatus] = mapped_column(Enum(GameStatus, name="game_status"))
    home_goals_90: Mapped[int | None] = mapped_column(Integer, default=None)
    away_goals_90: Mapped[int | None] = mapped_column(Integer, default=None)
    advancing_team_id: Mapped[int | None] = mapped_column(Integer, default=None)
    first_scorer_player_id: Mapped[int | None] = mapped_column(Integer, default=None)
    announced_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    goals_announced: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # Last time the reconcile job re-checked this game's result post-settlement (§8.3/§9.2).
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    bets: Mapped[list[Bet]] = relationship(back_populates="game", cascade="all, delete-orphan")
    ai_palpites: Mapped[list[AiPalpite]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )


class Bet(Base):
    """One prediction by one player on one fixture in one category (§6, §8.1)."""

    __tablename__ = "bets"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "player_telegram_id",
            "category",
            name="uq_bet_one_per_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("games.fixture_id"))
    player_telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_id"))
    category: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, default=None)
    points_awarded: Mapped[int | None] = mapped_column(Integer, default=None)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    game: Mapped[Game] = relationship(back_populates="bets")
    player: Mapped[Player] = relationship(back_populates="bets")


class ApiUsage(Base):
    """Per-budget-day provider request counter (§7.3)."""

    __tablename__ = "api_usage"

    budget_date: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class AiPalpite(Base):
    """A cached AI palpite for one fixture on one day (§20).

    One row per ``(fixture_id, palpite_date)`` (the cache key) holds the validated JSON of the
    whole per-game analysis. ``palpite_date`` is the local date the palpite was generated for,
    so a day's predictions are computed at most once and reused by every ``/palpite``.
    """

    __tablename__ = "ai_palpites"
    __table_args__ = (UniqueConstraint("fixture_id", "palpite_date", name="uq_palpite_per_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("games.fixture_id"))
    palpite_date: Mapped[date] = mapped_column(Date)
    payload_json: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    game: Mapped[Game] = relationship(back_populates="ai_palpites")


class Tournament(Base):
    """A bolãozinho: a real-money competition over a set of fixtures (Feature 7 / §22)."""

    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    # Money in integer minor units (cents); frozen once the first entry exists (uniform price).
    entry_price_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[TournamentStatus] = mapped_column(
        Enum(TournamentStatus, name="tournament_status")
    )
    created_by: Mapped[int] = mapped_column(BigInteger)  # authoritative for management permissions
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # Persisted, one-way lock set when the first member game kicks off (freezes games/price/joins).
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    result_announced_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # Stable hash of the announced outcome — detects a re-grade flipping the result (§7 correction).
    result_signature: Mapped[str | None] = mapped_column(String, default=None)
    correction_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    games: Mapped[list[TournamentGame]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )
    entries: Mapped[list[TournamentEntry]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )


class TournamentGame(Base):
    """Membership of a fixture in a bolãozinho (M:N — a game may be in many)."""

    __tablename__ = "tournament_games"

    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("games.fixture_id"), primary_key=True)

    tournament: Mapped[Tournament] = relationship(back_populates="games")


class TournamentEntry(Base):
    """A player's entry into a bolãozinho (one per player per tournament)."""

    __tablename__ = "tournament_entries"
    __table_args__ = (
        UniqueConstraint("tournament_id", "player_telegram_id", name="uq_entry_one_per_player"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    player_telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tournament: Mapped[Tournament] = relationship(back_populates="entries")
