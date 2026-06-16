"""add ai_palpites (AI palpite cache)

Revision ID: c1a2b3d4e5f6
Revises: 7f3a9c2b1e04
Create Date: 2026-06-16 10:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "7f3a9c2b1e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_palpites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("palpite_date", sa.Date(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["games.fixture_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fixture_id", "palpite_date", name="uq_palpite_per_day"),
    )


def downgrade() -> None:
    op.drop_table("ai_palpites")
