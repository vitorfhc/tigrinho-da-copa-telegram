"""Tests for the daily-bolãozinho orchestration service (§24)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from tigrinho.ai.daily_bolao import DailyBolaoScoring, GameInterestCriteria, GameInterestScore
from tigrinho.bot.runtime import AppContext
from tigrinho.daily_bolao_service import DailyBolaoError, create_daily_bolao
from tigrinho.db.models import (
    Game,
    GameStatus,
    Player,
    Stage,
    TournamentStatus,
)
from tigrinho.db.repositories import TournamentRepository

# America/Sao_Paulo (UTC-3): 2026-06-21 local day == [03:00 UTC 06-21, 03:00 UTC 06-22).
_TARGET = date(2026, 6, 21)
_NOW = datetime(2026, 6, 20, 21, 0)  # the evening before


def _crit(**over: bool) -> dict[str, bool]:
    base: dict[str, bool] = {
        "decisive": False,
        "quality_matchup": False,
        "rivalry_or_storyline": False,
        "star_power": False,
        "competitive_balance": False,
        "goal_potential": False,
    }
    base.update(over)
    return base


class FakeScorer:
    """A GameScorer returning canned scoring JSON (or a raw string / raising an error)."""

    def __init__(
        self,
        *,
        scores: dict[int, dict[str, bool]] | None = None,
        name: str = "Dois jogões",
        raw: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._scores = scores or {}
        self._name = name
        self._raw = raw
        self._exc = exc
        self.calls = 0

    async def score_games(self, *, system_instruction: str, user_content: str) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        if self._raw is not None:
            return self._raw
        items = [
            GameInterestScore(fixture_id=fid, criteria=GameInterestCriteria(**crit))
            for fid, crit in self._scores.items()
        ]
        return DailyBolaoScoring(name=self._name, scores=items).model_dump_json()


def _seed_game(app_context: AppContext, fid: int, kickoff: datetime) -> None:
    with app_context.session_factory() as s:
        s.add(
            Game(
                fixture_id=fid,
                match_hash=f"h{fid}",
                stage=Stage.GROUP,
                home_team_id=fid * 10,
                home_team_name=f"Home{fid}",
                away_team_id=fid * 10 + 1,
                away_team_name=f"Away{fid}",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
            )
        )
        s.commit()


def _seed_player(app_context: AppContext, telegram_id: int) -> None:
    with app_context.session_factory() as s:
        s.add(Player(telegram_id=telegram_id, display_name=f"P{telegram_id}"))
        s.commit()


async def test_creates_and_opens_top_two(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 13, 0))
    _seed_game(app_context, 2, datetime(2026, 6, 21, 16, 0))
    _seed_game(app_context, 3, datetime(2026, 6, 21, 19, 0))
    _seed_player(app_context, 555)
    scorer = FakeScorer(
        scores={
            1: _crit(decisive=True),  # interest 1
            2: _crit(decisive=True, quality_matchup=True, star_power=True),  # interest 3
            3: _crit(decisive=True, quality_matchup=True),  # interest 2
        },
        name="Clássicos da Quarta",
    )

    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )

    assert result.status == "created"
    assert result.reason == ""
    assert scorer.calls == 1
    assert result.tournament is not None
    with app_context.session_factory() as s:
        t = TournamentRepository(s).get(result.tournament.id)
        assert t is not None
        assert t.status is TournamentStatus.OPEN
        assert t.name == "Clássicos da Quarta"
        assert t.entry_price_cents == app_context.settings.daily_bolao_entry_price_cents
        assert t.created_by == app_context.settings.admin_user_id
        assert t.auto_created_for == _TARGET
        # top 2 by interest: fixtures 2 (3) and 3 (2)
        assert {g.fixture_id for g in TournamentRepository(s).list_games(t.id)} == {2, 3}
    # returned ORM games stay usable after the session closed (expire_on_commit=False)
    assert {g.fixture_id for g in result.games} == {2, 3}
    # one known player → at least one DM recipient passed back to the job
    assert 555 in {tid for tid, _ in result.mentions}


async def test_single_game_day_creates_one_game_pool(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(scores={1: _crit(goal_potential=True)})
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "created"
    assert result.tournament is not None
    with app_context.session_factory() as s:
        assert len(TournamentRepository(s).list_games(result.tournament.id)) == 1


async def test_zero_fixtures_skips_without_calling_scorer(app_context: AppContext) -> None:
    scorer = FakeScorer(scores={})
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "skipped"
    assert result.reason == "no fixtures"
    assert scorer.calls == 0


async def test_idempotent_skip_when_already_created(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    with app_context.session_factory() as s:
        t = TournamentRepository(s).create(name="já existe", entry_price_cents=1000, created_by=1)
        t.auto_created_for = _TARGET
        s.commit()
    scorer = FakeScorer(scores={1: _crit(decisive=True)})
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "skipped"
    assert result.reason == "exists"
    assert scorer.calls == 0  # short-circuits before Gemini


async def test_blank_name_falls_back_to_dated_name(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(scores={1: _crit(decisive=True)}, name="  [1]  ")
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "created"
    assert result.tournament is not None
    assert result.tournament.name == "Bolãozinho do dia 21/06"


async def test_scorer_error_raises_and_creates_nothing(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(exc=RuntimeError("gemini down"))
    with pytest.raises(RuntimeError):
        await create_daily_bolao(
            app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
        )
    with app_context.session_factory() as s:
        assert TournamentRepository(s).daily_auto_for(_TARGET) is None


async def test_only_hallucinated_ids_raises_no_fallback(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(scores={999: _crit(decisive=True)})  # 999 not a candidate
    with pytest.raises(DailyBolaoError):
        await create_daily_bolao(
            app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
        )
    with app_context.session_factory() as s:
        assert TournamentRepository(s).daily_auto_for(_TARGET) is None


async def test_unparseable_response_raises_and_creates_nothing(app_context: AppContext) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(raw="this is not json")
    with pytest.raises(ValueError):
        await create_daily_bolao(
            app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
        )
    with app_context.session_factory() as s:
        assert TournamentRepository(s).daily_auto_for(_TARGET) is None
