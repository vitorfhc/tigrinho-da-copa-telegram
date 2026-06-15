"""Apply a MatchResult to the database: write the game result + grade every bet (§8.3).

This is the DB-writing counterpart to the pure ``domain/settlement.py``. It is shared by the poll
job (auto-settlement) and the Typer CLI (manual re-settle), so it lives outside the bot layer and
has **no Telegram dependency**. It is idempotent — re-running on the same inputs reproduces the
same stored grades (the pure ``settle_game`` is deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from tigrinho.db.models import Game, GameStatus, utcnow
from tigrinho.db.repositories import BetRepository, PlayerRepository
from tigrinho.domain.bets import BetCategory
from tigrinho.domain.scoring import first_genuine_scorer
from tigrinho.domain.settlement import PendingBet, settle_game
from tigrinho.providers.base import MatchResult


@dataclass(frozen=True, slots=True)
class CategoryResult:
    category: BetCategory
    is_correct: bool
    points: int


@dataclass(frozen=True, slots=True)
class PlayerResult:
    telegram_id: int
    display_name: str
    total_points: int
    categories: tuple[CategoryResult, ...]


@dataclass(frozen=True, slots=True)
class SettlementSummary:
    """Everything needed to build the group results message (Telegram-agnostic)."""

    fixture_id: int
    home_team_name: str
    away_team_name: str
    home_goals_90: int
    away_goals_90: int
    first_scorer_player_id: int | None
    players: tuple[PlayerResult, ...]


def settle_fixture(session: Session, game: Game, result: MatchResult) -> SettlementSummary:
    """Grade all bets for ``game`` against ``result``; persist the result + grades (idempotent)."""
    bet_repo = BetRepository(session)
    player_repo = PlayerRepository(session)
    bets = bet_repo.list_for_game(game.fixture_id)

    pending = [PendingBet(b.id, BetCategory(b.category), b.payload_json) for b in bets]
    graded = {
        g.bet_id: g
        for g in settle_game(
            pending, result, home_team_id=game.home_team_id, away_team_id=game.away_team_id
        )
    }

    now = utcnow()
    per_player: dict[int, list[CategoryResult]] = {}
    for bet in bets:
        grade = graded[bet.id]
        bet.is_correct = grade.is_correct
        bet.points_awarded = grade.points
        bet.settled_at = now
        per_player.setdefault(bet.player_telegram_id, []).append(
            CategoryResult(BetCategory(bet.category), grade.is_correct, grade.points)
        )

    # ``settle_game`` already validated the 90′ score is present.
    assert result.home_goals_90 is not None
    assert result.away_goals_90 is not None
    first = first_genuine_scorer(result.goals)
    game.home_goals_90 = result.home_goals_90
    game.away_goals_90 = result.away_goals_90
    game.advancing_team_id = result.advancing_team_id
    game.first_scorer_player_id = first.player_id if first is not None else None
    game.status = GameStatus.FINISHED
    game.settled_at = now
    session.flush()

    players: list[PlayerResult] = []
    for telegram_id, categories in per_player.items():
        player = player_repo.get(telegram_id)
        players.append(
            PlayerResult(
                telegram_id=telegram_id,
                display_name=player.display_name if player is not None else str(telegram_id),
                total_points=sum(c.points for c in categories),
                categories=tuple(categories),
            )
        )
    players.sort(key=lambda p: (-p.total_points, p.display_name))

    return SettlementSummary(
        fixture_id=game.fixture_id,
        home_team_name=game.home_team_name,
        away_team_name=game.away_team_name,
        home_goals_90=result.home_goals_90,
        away_goals_90=result.away_goals_90,
        first_scorer_player_id=game.first_scorer_player_id,
        players=tuple(players),
    )
