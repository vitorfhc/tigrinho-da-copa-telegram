"""Daily AI-curated bolãozinho orchestration (COMPLETION.md §24).

Telegram-free: query tomorrow's SCHEDULED games, ask the (separate) Gemini scorer to grade each
on six binary criteria, rank by count-of-trues in pure domain code, and create + open a
bolãozinho over the best ≤2. Network (the Gemini call) is async; the SQLite reads/writes are
synchronous (the project's split). There is NO fallback: a Gemini/parse failure or zero usable
picks raises, and the caller (the job) DMs the admin. Idempotency is guaranteed by the UNIQUE
``auto_created_for`` column (pre-check + IntegrityError on commit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tigrinho import tournament_service as svc
from tigrinho.ai.base import GameScorer
from tigrinho.ai.daily_bolao import (
    GameInterestCriteria,
    build_scoring_prompt,
    parse_scoring,
    sanitize_name,
)
from tigrinho.ai.prompt import GameInfo
from tigrinho.config import Settings
from tigrinho.db.models import Game, Tournament
from tigrinho.db.repositories import GameRepository, PlayerRepository, TournamentRepository
from tigrinho.domain.daily_bolao import (
    Candidate,
    InterestCriteria,
    interest,
    local_day_window_utc,
    rank_and_select,
)
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.daily_bolao_service")

_MAX_GAMES = 2


class DailyBolaoError(Exception):
    """A genuine failure (no fallback) — the caller DMs the admin and creates nothing."""


@dataclass(frozen=True, slots=True)
class DailyBolaoResult:
    """Outcome of one daily run; ``games``/``mentions`` feed the open announcement."""

    status: Literal["created", "skipped"]
    reason: str = ""
    tournament: Tournament | None = None
    games: tuple[Game, ...] = field(default_factory=tuple)
    mentions: tuple[tuple[int, str], ...] = field(default_factory=tuple)


def _to_domain(c: GameInterestCriteria) -> InterestCriteria:
    return InterestCriteria(
        decisive=c.decisive,
        quality_matchup=c.quality_matchup,
        rivalry_or_storyline=c.rivalry_or_storyline,
        star_power=c.star_power,
        competitive_balance=c.competitive_balance,
        goal_potential=c.goal_potential,
    )


def _skipped(reason: str) -> DailyBolaoResult:
    return DailyBolaoResult(status="skipped", reason=reason)


async def create_daily_bolao(
    session_factory: sessionmaker[Session],
    scorer: GameScorer,
    settings: Settings,
    *,
    now: datetime,
    target_date: date,
) -> DailyBolaoResult:
    """Create + auto-open the daily AI bolãozinho for ``target_date`` (or skip / raise)."""
    start_utc, end_utc = local_day_window_utc(target_date, settings.tzinfo)

    # Session 1: idempotency pre-check + read candidates.
    with session_factory() as session:
        if TournamentRepository(session).daily_auto_for(target_date) is not None:
            return _skipped("exists")
        games = GameRepository(session).list_scheduled_in_window(start_utc, end_utc)
        candidates = [Candidate(fixture_id=g.fixture_id, kickoff_utc=g.kickoff_utc) for g in games]
        infos = [
            GameInfo(
                fixture_id=g.fixture_id,
                home_team=g.home_team_name,
                away_team=g.away_team_name,
                kickoff_local=g.kickoff_local,
                stage=g.stage,
            )
            for g in games
        ]

    if not candidates:
        return _skipped("no fixtures")

    # Gemini call (async, outside any session). Raises on failure → no fallback.
    system_instruction, user_content = build_scoring_prompt(infos)
    raw = await scorer.score_games(system_instruction=system_instruction, user_content=user_content)
    batch = parse_scoring(raw)
    scores = {s.fixture_id: interest(_to_domain(s.criteria)) for s in batch.scores}
    picks = rank_and_select(candidates, scores, limit=_MAX_GAMES)
    if not picks:
        raise DailyBolaoError("Gemini não pontuou nenhum jogo válido")
    _log.info("daily_bolao_scored", candidates=len(candidates), scored=len(scores), picks=picks)
    name = sanitize_name(batch.name) or f"Bolãozinho do dia {target_date:%d/%m}"

    # Session 2: create + open. UNIQUE constraint + IntegrityError is the real idempotency guard.
    with session_factory() as session:
        tournament = svc.create_tournament(
            session,
            name=name,
            entry_price_cents=settings.daily_bolao_entry_price_cents,
            created_by=settings.admin_user_id,
        )
        tournament.auto_created_for = target_date
        for fixture_id in picks:
            svc.add_game(session, tournament, fixture_id, now=now)
        svc.open_tournament(
            session, tournament, now=now, splitwise_enabled=settings.splitwise_enabled
        )
        out_games = tuple(TournamentRepository(session).list_games(tournament.id))
        mentions = tuple(
            (p.telegram_id, p.display_name) for p in PlayerRepository(session).list_all()
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return _skipped("exists")

    return DailyBolaoResult(
        status="created", tournament=tournament, games=out_games, mentions=mentions
    )
