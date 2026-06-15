"""Load settled-bet records for the scoreboard (COMPLETION.md §10).

DB glue between the persistence layer and the pure :mod:`tigrinho.scoreboard`. Telegram-free so
both the bot's ``/placar`` and the CLI's board rebuild share it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import GameStatus
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.domain.bets import BetCategory
from tigrinho.scoreboard import BetRecord, in_current_week


def load_board_records(session: Session, *, weekly: bool, now_local: datetime) -> list[BetRecord]:
    """Project settled bets into ranking records (skips voided games; filters to the week)."""
    games = GameRepository(session)
    players = PlayerRepository(session)
    records: list[BetRecord] = []
    for bet in BetRepository(session).list_settled():
        if bet.points_awarded is None:
            continue
        game = games.get(bet.fixture_id)
        if game is None or game.status is GameStatus.VOID:
            continue
        if weekly and not in_current_week(game.kickoff_local, now_local):
            continue
        player = players.get(bet.player_telegram_id)
        if player is None:
            continue
        correct = bool(bet.is_correct)
        records.append(
            BetRecord(
                telegram_id=player.telegram_id,
                display_name=player.display_name,
                created_at=player.created_at,
                points=bet.points_awarded,
                is_correct=correct,
                is_exact_score_hit=correct and bet.category == BetCategory.EXACT_SCORE.value,
            )
        )
    return records
