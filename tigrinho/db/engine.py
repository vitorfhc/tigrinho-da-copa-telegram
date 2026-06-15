"""SQLAlchemy engine + session factory (synchronous; COMPLETION.md §3, §5).

Local SQLite queries are sub-millisecond, so the data layer is synchronous and shared verbatim
with the Typer CLI. ``PRAGMA foreign_keys=ON`` is enabled per connection so SQLite actually
enforces the foreign keys declared on the models.

Grounding (per §2): SQLAlchemy 2.0 — https://docs.sqlalchemy.org/en/20/orm/session_basics.html
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tigrinho.db.models import Base


def _sqlite_url(db_path: str) -> str:
    if db_path == ":memory:":
        return "sqlite:///:memory:"
    return f"sqlite:///{db_path}"


def create_db_engine(db_path: str, *, echo: bool = False) -> Engine:
    """Create a synchronous SQLAlchemy engine for the SQLite file at ``db_path``."""
    engine = create_engine(_sqlite_url(db_path), echo=echo)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory; ``expire_on_commit=False`` keeps objects usable post-commit."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Create every table from the ORM metadata (tests/local; production uses Alembic)."""
    Base.metadata.create_all(engine)
