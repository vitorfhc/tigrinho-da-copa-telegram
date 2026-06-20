"""add games.home_goals_announced + games.away_goals_announced (live-goal split cursor)

Splits the live-goal cursor by side so the live poll can name the scoring team straight from the
live score feed (``get_live_results``), without the slower ``/fixtures/events`` lookup (§9.4).
Existing rows default both to 0; a match live mid-deploy may re-announce its current goals on the
first post-deploy cycle (same cold-start behaviour as the existing ``goals_announced`` cursor).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("home_goals_announced", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("away_goals_announced", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.drop_column("away_goals_announced")
        batch_op.drop_column("home_goals_announced")
