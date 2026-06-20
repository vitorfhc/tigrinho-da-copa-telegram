"""AI palpite generation + caching service (COMPLETION.md §20).

Telegram-free orchestration shared by the ``/palpite`` command and the daily 06h job:

- :func:`generate_palpites` finds the palpite-eligible games — upcoming (next 24h) **and**
  in-progress (LIVE) — that do **not** yet have a palpite cached for ``palpite_date``, asks the
  generator once for those, validates the JSON, and stores one row per game. Because it only ever
  fills the gaps, the day's predictions are computed at most once (the DB is the cache).
- :func:`load_today_palpites` reads the cached palpites for today's eligible games (for display).

Network work (the Gemini call) is ``async``; the SQLite reads/writes are synchronous (the
project's split). The generator is injected so this layer never imports ``google-genai``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from tigrinho.ai.base import PalpiteGenerator
from tigrinho.ai.prompt import GameInfo, build_palpite_prompt
from tigrinho.ai.schemas import GamePalpite, parse_batch
from tigrinho.db.repositories import GameRepository, PalpiteRepository
from tigrinho.enums import CategorySet

# The AI analyzes the games kicking off in the next 24h (the same horizon as the morning announce).
PALPITE_HORIZON = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class RenderablePalpite:
    """A cached palpite joined with its game's display fields, ready to render."""

    fixture_id: int
    home_team: str
    away_team: str
    kickoff_local: datetime
    palpite: GamePalpite
    # The game's bet-category regime, so the renderer shows only the categories it offers (§8.1).
    category_set: CategorySet


async def generate_palpites(
    session_factory: sessionmaker[Session],
    generator: PalpiteGenerator,
    *,
    now: datetime,
    palpite_date: date,
    live_window_hours: int,
) -> list[int]:
    """Generate + cache palpites for eligible games missing one for ``palpite_date``.

    Eligible = upcoming (next 24h) plus in-progress (LIVE within ``live_window_hours``). Returns
    the fixture ids that were generated this call (empty if the cache was already warm, in which
    case the generator is never invoked).
    """
    with session_factory() as session:
        games = GameRepository(session).list_palpite_games(now, PALPITE_HORIZON, live_window_hours)
        cached = PalpiteRepository(session).existing_fixture_ids(
            [g.fixture_id for g in games], palpite_date
        )
        infos = [
            GameInfo(
                fixture_id=g.fixture_id,
                home_team=g.home_team_name,
                away_team=g.away_team_name,
                kickoff_local=g.kickoff_local,
                stage=g.stage,
            )
            for g in games
            if g.fixture_id not in cached
        ]

    if not infos:
        return []

    system_instruction, user_content = build_palpite_prompt(infos)
    raw = await generator.generate(system_instruction=system_instruction, user_content=user_content)
    batch = parse_batch(raw)

    requested = {info.fixture_id for info in infos}
    saved: list[int] = []
    with session_factory() as session:
        repo = PalpiteRepository(session)
        for game_palpite in batch.palpites:
            if game_palpite.fixture_id not in requested:
                continue  # ignore hallucinated fixtures not in our request
            repo.upsert(
                fixture_id=game_palpite.fixture_id,
                palpite_date=palpite_date,
                payload_json=game_palpite.model_dump_json(),
            )
            saved.append(game_palpite.fixture_id)
        session.commit()
    return saved


def load_today_palpites(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
    palpite_date: date,
    live_window_hours: int,
) -> list[RenderablePalpite]:
    """Load cached palpites for the eligible games (skipping games without one), soonest first."""
    with session_factory() as session:
        games = GameRepository(session).list_palpite_games(now, PALPITE_HORIZON, live_window_hours)
        rows = {
            row.fixture_id: row
            for row in PalpiteRepository(session).list_for_date(
                [g.fixture_id for g in games], palpite_date
            )
        }
        rendered: list[RenderablePalpite] = []
        for game in games:
            row = rows.get(game.fixture_id)
            if row is None:
                continue
            rendered.append(
                RenderablePalpite(
                    fixture_id=game.fixture_id,
                    home_team=game.home_team_name,
                    away_team=game.away_team_name,
                    kickoff_local=game.kickoff_local,
                    palpite=GamePalpite.model_validate_json(row.payload_json),
                    category_set=game.category_set,
                )
            )
    return rendered
