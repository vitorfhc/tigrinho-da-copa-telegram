"""Alembic migration environment (COMPLETION.md §3, §15).

Grounding (per §2), verified June 2026:
- Alembic 1.18 — https://alembic.sqlalchemy.org/en/latest/batch.html
  ``render_as_batch=True`` enables SQLite's "move and copy" ALTER workaround. The schema is
  taken from ``Base.metadata`` (autogenerate target). The DB URL is resolved at runtime, never
  baked into ``alembic.ini``.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from tigrinho.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the SQLite URL: explicit ini override → TIGRINHO_DB_URL → Settings.db_path."""
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    env_url = os.environ.get("TIGRINHO_DB_URL")
    if env_url:
        return env_url
    # Lazy import: only load full Settings when actually migrating a real deployment.
    from tigrinho.config import get_settings

    db_path = get_settings().db_path
    return "sqlite:///:memory:" if db_path == ":memory:" else f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Emit migration SQL without a live DB connection ('offline' mode)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection ('online' mode)."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
