"""Tests for the daily 06h AI palpite generation job (COMPLETION.md §20)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ContextTypes, JobQueue

from tigrinho.ai.schemas import GamePalpite
from tigrinho.bot.palpite_job import PALPITE_JOB_NAME, palpite_job, schedule_palpite_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import PalpiteRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _palpite_json(fixture_id: int) -> str:
    return GamePalpite(
        fixture_id=fixture_id,
        analysis="a",
        exact_score={"home": 1, "away": 0},  # type: ignore[arg-type]
        first_team="HOME",  # type: ignore[arg-type]
        btts="ONLY_HOME",  # type: ignore[arg-type]
        winner="HOME",  # type: ignore[arg-type]
        over_under="UNDER",  # type: ignore[arg-type]
    ).model_dump_json()


class FakeGenerator:
    def __init__(self, fixture_ids: list[int]) -> None:
        self._fixture_ids = fixture_ids
        self.calls = 0

    async def generate(self, *, system_instruction: str, user_content: str) -> str:
        self.calls += 1
        items = ", ".join(_palpite_json(fid) for fid in self._fixture_ids)
        return '{"palpites": [' + items + "]}"


def _seed_game(app_context: AppContext, fixture_id: int) -> None:
    kickoff = _now() + timedelta(hours=3)
    with app_context.session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=fixture_id * 10,
                home_team_name=f"Home{fixture_id}",
                away_team_id=fixture_id * 10 + 1,
                away_team_name=f"Away{fixture_id}",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
            )
        )
        session.commit()


def _ctx(app_context: AppContext) -> tuple[ContextTypes.DEFAULT_TYPE, AsyncMock]:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    bot = AsyncMock()
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx), bot


async def test_no_key_skips_silently(app_context: AppContext) -> None:
    _seed_game(app_context, 1)  # has no generator
    ctx, bot = _ctx(app_context)
    await palpite_job(ctx)
    bot.send_message.assert_not_awaited()  # no admin DM, no group post
    with app_context.session_factory() as s:
        assert PalpiteRepository(s).get(1, datetime.now(app_context.settings.tzinfo).date()) is None


async def test_generates_and_caches_without_posting_to_group(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    _seed_game(app_context, 2)
    gen = FakeGenerator([1, 2])
    ctx, bot = _ctx(dataclasses.replace(app_context, palpite_generator=gen))

    await palpite_job(ctx)

    assert gen.calls == 1
    bot.send_message.assert_not_awaited()  # the 06h job only warms the cache, it does not post
    today = datetime.now(app_context.settings.tzinfo).date()
    with app_context.session_factory() as s:
        assert PalpiteRepository(s).existing_fixture_ids([1, 2], today) == {1, 2}


async def test_generation_failure_alerts_admin(app_context: AppContext) -> None:
    _seed_game(app_context, 1)

    class BoomGenerator:
        async def generate(self, *, system_instruction: str, user_content: str) -> str:
            raise RuntimeError("gemini down")

    ctx, bot = _ctx(
        dataclasses.replace(app_context, palpite_generator=cast(FakeGenerator, BoomGenerator()))
    )

    await palpite_job(ctx)

    bot.send_message.assert_awaited()  # admin DM about the failure
    assert bot.send_message.await_args.kwargs["chat_id"] == app_context.settings.admin_user_id


def test_schedule_palpite_job(settings: Settings) -> None:
    job_queue = MagicMock()
    schedule_palpite_job(cast("JobQueue[ContextTypes.DEFAULT_TYPE]", job_queue), settings)
    job_queue.run_daily.assert_called_once()
    kwargs = job_queue.run_daily.call_args.kwargs
    assert kwargs["name"] == PALPITE_JOB_NAME
    assert kwargs["time"].hour == settings.palpite_time_obj.hour
