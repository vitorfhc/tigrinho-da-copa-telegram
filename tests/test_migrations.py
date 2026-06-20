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


def test_category_set_backfill(tmp_path: Path) -> None:
    """Games with ≥1 bet become LEGACY; games with no bets stay V2 (the new-set rollout, §8.1)."""
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = _alembic_config(db_url)
    # Bring the DB up to the revision *just before* this migration, then seed games + a bet.
    command.upgrade(cfg, "d4e5f6a7b8c9")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for fid in (100, 200):
                conn.execute(
                    text(
                        "INSERT INTO games (fixture_id, match_hash, stage, home_team_id, "
                        "home_team_name, away_team_id, away_team_name, kickoff_utc, kickoff_local, "
                        "status) VALUES (:fid, 'h', 'GROUP', 1, 'A', 2, 'B', "
                        "'2026-06-25 00:00:00', '2026-06-25 00:00:00', 'SCHEDULED')"
                    ),
                    {"fid": fid},
                )
            conn.execute(
                text(
                    "INSERT INTO players (telegram_id, display_name, created_at) "
                    "VALUES (1, 'P', '2026-06-01 00:00:00')"
                )
            )
            # Only game 100 has a bet.
            conn.execute(
                text(
                    "INSERT INTO bets (fixture_id, player_telegram_id, category, payload_json, "
                    "created_at, updated_at) VALUES (100, 1, 'WINNER', '{\"sel\": \"HOME\"}', "
                    "'2026-06-01 00:00:00', '2026-06-01 00:00:00')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT fixture_id, category_set FROM games")).all()
        regimes = {row[0]: row[1] for row in rows}
        assert regimes == {100: "LEGACY", 200: "V2"}
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
