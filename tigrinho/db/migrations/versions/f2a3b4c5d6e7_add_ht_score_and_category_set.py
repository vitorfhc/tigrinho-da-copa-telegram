"""add games.home_goals_ht/away_goals_ht + per-game category_set (orthogonal bet rollout, §8.1)

Adds the half-time score columns (for HALF_TIME_RESULT grading + re-settle) and a per-game
``category_set`` deciding which bet categories the game *offers*. Backfill rollout: any game that
already has ≥1 bet stays on the original five markets (``LEGACY``) so those bets keep grading and
rendering and a single game never mixes regimes; every game with no bets yet — plus all future
games (the column default) — uses the new two-market set (``V2`` = EXACT_SCORE + HALF_TIME_RESULT).

Revision ID: f2a3b4c5d6e7
Revises: d4e5f6a7b8c9
Create Date: 2026-06-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.add_column(sa.Column("home_goals_ht", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("away_goals_ht", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "category_set",
                sa.Enum("LEGACY", "V2", name="category_set"),
                nullable=False,
                server_default="V2",
            )
        )
    # Rollout: games that already have bets keep the OLD set; everything else stays V2 (default).
    op.execute(
        "UPDATE games SET category_set = 'LEGACY' "
        "WHERE fixture_id IN (SELECT DISTINCT fixture_id FROM bets)"
    )


def downgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.drop_column("category_set")
        batch_op.drop_column("away_goals_ht")
        batch_op.drop_column("home_goals_ht")
