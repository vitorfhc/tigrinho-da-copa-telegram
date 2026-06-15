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

import enum
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


def utcnow() -> datetime:
    """Current time as a naive UTC datetime (the project-wide storage convention)."""
    return datetime.now(tz=UTC).replace(tzinfo=None)


class Stage(enum.StrEnum):
    """Tournament stage of a fixture (drives the knockout winner rule, §8.1)."""

    GROUP = "GROUP"
    KNOCKOUT = "KNOCKOUT"


class GameStatus(enum.StrEnum):
    """Normalized provider status (§7.2)."""

    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    VOID = "VOID"


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
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    bets: Mapped[list[Bet]] = relationship(back_populates="game", cascade="all, delete-orphan")


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


class SquadPlayer(Base):
    """A cached squad member used for first-scorer selection (§6, seeded via CLI)."""

    __tablename__ = "squad_players"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String)
    position: Mapped[str | None] = mapped_column(String, default=None)


class ApiUsage(Base):
    """Per-budget-day provider request counter (§7.3)."""

    __tablename__ = "api_usage"

    budget_date: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
