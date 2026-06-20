"""add tournaments.auto_created_for + unique constraint (daily AI bolãozinho)

Marks a bolãozinho as the daily AI-curated pool for a local calendar date (§24). NULL for every
manually created bolãozinho; UNIQUE so two concurrent daily-job fires (or a redeploy overlapping
the run time) cannot create two pots for the same day. Existing rows default to NULL.

Revision ID: f7a8b9c0d1e2
Revises: d4e5f6a7b8c9
Create Date: 2026-06-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tournaments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("auto_created_for", sa.Date(), nullable=True))
        batch_op.create_unique_constraint("uq_tournament_auto_created_for", ["auto_created_for"])


def downgrade() -> None:
    with op.batch_alter_table("tournaments", schema=None) as batch_op:
        batch_op.drop_constraint("uq_tournament_auto_created_for", type_="unique")
        batch_op.drop_column("auto_created_for")
