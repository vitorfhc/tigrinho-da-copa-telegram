"""Alembic migration tests: `upgrade head` builds a schema matching the ORM (§6, §16)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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


def test_splitwise_transition_data_fix(tmp_path: Path) -> None:
    """Closed bolãozinhos become EXCLUDED at deploy; OPEN/DRAFT stay MANUAL (§23 transition)."""
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = _alembic_config(db_url)
    # Bring the DB up to the revision *just before* the Splitwise migration, then seed old rows.
    command.upgrade(cfg, "b2c3d4e5f6a7")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for tid, name, status in [
                (1, "Aberto", "OPEN"),
                (2, "Rascunho", "DRAFT"),
                (3, "Encerrado", "FINISHED"),
                (4, "Cancelado", "CANCELLED"),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO tournaments "
                        "(id, name, entry_price_cents, status, created_by, created_at) "
                        "VALUES (:id, :name, 1000, :status, 1, '2026-06-01 00:00:00')"
                    ),
                    {"id": tid, "name": name, "status": status},
                )
    finally:
        engine.dispose()

    # Apply the Splitwise migration.
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, splitwise_mode FROM tournaments")).all()
        modes = {row[0]: row[1] for row in rows}
        assert modes == {1: "MANUAL", 2: "MANUAL", 3: "EXCLUDED", 4: "EXCLUDED"}
    finally:
        engine.dispose()


def test_tournaments_auto_created_for_unique_after_upgrade(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    command.upgrade(_alembic_config(db_url), "head")
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("tournaments")}
        assert "auto_created_for" in cols
        uniques = inspector.get_unique_constraints("tournaments")
        names = {uc["name"] for uc in uniques}
        assert "uq_tournament_auto_created_for" in names
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
