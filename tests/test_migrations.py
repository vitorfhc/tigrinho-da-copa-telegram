"""Alembic migration tests: `upgrade head` builds a schema matching the ORM (§6, §16)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from tigrinho.db.models import Base

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "tigrinho" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_head_matches_orm_metadata(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    command.upgrade(_alembic_config(db_url), "head")

    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = set(Base.metadata.tables) | {"alembic_version"}
        assert expected == tables

        for name, table in Base.metadata.tables.items():
            migrated_cols = {col["name"] for col in inspector.get_columns(name)}
            assert {col.name for col in table.columns} == migrated_cols
    finally:
        engine.dispose()


def test_bets_unique_constraint_present_after_upgrade(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    command.upgrade(_alembic_config(db_url), "head")

    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        uniques = inspector.get_unique_constraints("bets")
        cols = {tuple(uc["column_names"]) for uc in uniques}
        assert ("fixture_id", "player_telegram_id", "category") in cols
    finally:
        engine.dispose()


def test_downgrade_base_drops_model_tables(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(db_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables.isdisjoint(set(Base.metadata.tables))
    finally:
        engine.dispose()
