"""Load settled-bet records for the scoreboard (COMPLETION.md §10).

DB glue between the persistence layer and the pure :mod:`tigrinho.scoreboard`. Telegram-free so
both the bot's ``/placar`` and the CLI's board rebuild share it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.db.models import Bet, GameStatus, Player
from tigrinho.db.repositories import BetRepository, GameRepository, PlayerRepository
from tigrinho.domain.bets import BetCategory
from tigrinho.scoreboard import BetRecord, in_current_week


def _record(player: Player, bet: Bet) -> BetRecord:
    """Project a settled bet + its player into a ranking record (shared by both loaders)."""
    correct = bool(bet.is_correct)
    assert bet.points_awarded is not None  # callers filter unsettled bets out
    return BetRecord(
        telegram_id=player.telegram_id,
        display_name=player.display_name,
        created_at=player.created_at,
        points=bet.points_awarded,
        is_correct=correct,
        is_exact_score_hit=correct and bet.category == BetCategory.EXACT_SCORE.value,
    )


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
        records.append(_record(player, bet))
    return records


def load_game_records(session: Session, fixture_id: int) -> list[BetRecord]:
    """Project one finished game's settled bets into ranking records (per-game board, §10)."""
    game = GameRepository(session).get(fixture_id)
    if game is None or game.status is GameStatus.VOID:
        return []
    players = PlayerRepository(session)
    records: list[BetRecord] = []
    for bet in BetRepository(session).list_for_game(fixture_id):
        if bet.settled_at is None or bet.points_awarded is None:
            continue
        player = players.get(bet.player_telegram_id)
        if player is None:
            continue
        records.append(_record(player, bet))
    return records


def load_games_records(session: Session, fixture_ids: Sequence[int]) -> list[BetRecord]:
    """Project several finished games' settled bets into one record list (combined board, §10).

    Delegates to :func:`load_game_records` per fixture (so ``VOID`` and unsettled bets are skipped),
    then concatenates; :func:`tigrinho.scoreboard.rank` sums each player across the set.
    """
    records: list[BetRecord] = []
    for fixture_id in fixture_ids:
        records.extend(load_game_records(session, fixture_id))
    return records
