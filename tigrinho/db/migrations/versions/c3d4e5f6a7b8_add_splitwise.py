"""add Splitwise columns + transition data-fix (Feature 8, §23)

Adds the player link columns and the per-bolãozinho Splitwise policy/state columns, then stamps
existing rows: closed bolãozinhos (FINISHED/CANCELLED) become EXCLUDED so the bot never touches the
ones already settled by hand; OPEN/DRAFT stay MANUAL (trackable for later manual registration). New
rows default to MANUAL and are promoted to AUTO at open time when the feature is enabled.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-19 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.add_column(sa.Column("splitwise_user_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("splitwise_email", sa.String(), nullable=True))
    with op.batch_alter_table("tournaments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "splitwise_mode",
                sa.Enum("AUTO", "MANUAL", "EXCLUDED", name="splitwise_mode"),
                nullable=False,
                server_default="MANUAL",
            )
        )
        batch_op.add_column(sa.Column("splitwise_expense_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("splitwise_synced_signature", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("splitwise_admin_notified_at", sa.DateTime(), nullable=True))
    # Transition: closed bolãozinhos are out of scope (covers the ones already settled by hand).
    op.execute(
        "UPDATE tournaments SET splitwise_mode = 'EXCLUDED' "
        "WHERE status IN ('FINISHED', 'CANCELLED')"
    )


def downgrade() -> None:
    with op.batch_alter_table("tournaments", schema=None) as batch_op:
        batch_op.drop_column("splitwise_admin_notified_at")
        batch_op.drop_column("splitwise_synced_signature")
        batch_op.drop_column("splitwise_expense_id")
        batch_op.drop_column("splitwise_mode")
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.drop_column("splitwise_email")
        batch_op.drop_column("splitwise_user_id")
