"""Tests for the daily-bolãozinho run_daily job (§24)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ContextTypes

from tigrinho.ai.daily_bolao import DailyBolaoScoring, GameInterestCriteria, GameInterestScore
from tigrinho.bot.daily_bolao_job import daily_bolao_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import TournamentRepository
from tigrinho.domain.daily_bolao import local_day_window_utc


def _ctx(app_context: AppContext) -> tuple[ContextTypes.DEFAULT_TYPE, AsyncMock]:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    bot = AsyncMock()
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx), bot


def _crit() -> GameInterestCriteria:
    return GameInterestCriteria(
        decisive=True,
        quality_matchup=True,
        rivalry_or_storyline=False,
        star_power=True,
        competitive_balance=False,
        goal_potential=True,
    )


class FakeScorer:
    def __init__(self, fixture_ids: list[int], *, exc: Exception | None = None) -> None:
        self._fixture_ids = fixture_ids
        self._exc = exc
        self.calls = 0

    async def score_games(self, *, system_instruction: str, user_content: str) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        scores = [GameInterestScore(fixture_id=f, criteria=_crit()) for f in self._fixture_ids]
        return DailyBolaoScoring(name="Bolão de Teste", scores=scores).model_dump_json()


def _tomorrow_kickoff(app_context: AppContext) -> datetime:
    tz = app_context.settings.tzinfo
    target = (datetime.now(tz) + timedelta(days=1)).date()
    start, end = local_day_window_utc(target, tz)
    return start + (end - start) / 2  # midday-ish, safely inside the window


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


async def test_noop_when_no_scorer(app_context: AppContext) -> None:
    ctx, bot = _ctx(dataclasses.replace(app_context, game_scorer=None))
    await daily_bolao_job(ctx)
    bot.send_message.assert_not_awaited()


async def test_creates_and_announces(app_context: AppContext) -> None:
    kickoff = _tomorrow_kickoff(app_context)
    _seed_game(app_context, 1, kickoff)
    _seed_game(app_context, 2, kickoff + timedelta(hours=3))
    scorer = FakeScorer([1, 2])
    ctx, bot = _ctx(dataclasses.replace(app_context, game_scorer=scorer))

    await daily_bolao_job(ctx)

    assert scorer.calls == 1
    # group announcement was posted
    chat_ids = [c.kwargs.get("chat_id") for c in bot.send_message.await_args_list]
    assert app_context.settings.group_chat_id in chat_ids
    with app_context.session_factory() as s:
        target = (datetime.now(app_context.settings.tzinfo) + timedelta(days=1)).date()
        assert TournamentRepository(s).daily_auto_for(target) is not None


async def test_failure_dms_admin_and_does_not_crash(app_context: AppContext) -> None:
    _seed_game(app_context, 1, _tomorrow_kickoff(app_context))
    scorer = FakeScorer([1], exc=RuntimeError("boom"))
    ctx, bot = _ctx(dataclasses.replace(app_context, game_scorer=scorer))

    await daily_bolao_job(ctx)  # must NOT raise

    admin_id = app_context.settings.admin_user_id
    assert any(c.kwargs.get("chat_id") == admin_id for c in bot.send_message.await_args_list)
