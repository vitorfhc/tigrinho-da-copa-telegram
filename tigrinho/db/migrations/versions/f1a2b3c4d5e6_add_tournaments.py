"""add bolãozinho (tournament) tables (Feature 7 / §22)

Revision ID: f1a2b3c4d5e6
Revises: e3f4a5b6c7d8
Create Date: 2026-06-18 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tournaments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("entry_price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "OPEN", "FINISHED", "CANCELLED", name="tournament_status"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("result_announced_at", sa.DateTime(), nullable=True),
        sa.Column("result_signature", sa.String(), nullable=True),
        sa.Column("correction_count", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tournament_games",
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["games.fixture_id"]),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"]),
        sa.PrimaryKeyConstraint("tournament_id", "fixture_id"),
    )
    op.create_table(
        "tournament_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("player_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_telegram_id"], ["players.telegram_id"]),
        sa.ForeignKeyConstraint(["tournament_id"], ["tournaments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tournament_id", "player_telegram_id", name="uq_entry_one_per_player"),
    )


def downgrade() -> None:
    op.drop_table("tournament_entries")
    op.drop_table("tournament_games")
    op.drop_table("tournaments")
