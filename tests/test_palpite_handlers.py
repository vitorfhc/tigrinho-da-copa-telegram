"""Tests for the /palpite command handler (COMPLETION.md §20)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.ext import ContextTypes

from tigrinho.ai.schemas import GamePalpite
from tigrinho.bot.palpite_handlers import palpite_handler
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import PalpiteRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _palpite_json(fixture_id: int) -> str:
    return GamePalpite(
        fixture_id=fixture_id,
        analysis="análise",
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


def _with_generator(app_context: AppContext, gen: FakeGenerator) -> AppContext:
    return dataclasses.replace(app_context, palpite_generator=gen)


def _ctx(app_context: AppContext) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    ctx.args = None
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _update() -> tuple[Update, AsyncMock]:
    message = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    return cast(Update, update), message


async def test_no_key_reports_error(app_context: AppContext) -> None:
    # app_context fixture has no palpite_generator (feature disabled).
    update, message = _update()
    await palpite_handler(update, _ctx(app_context))
    message.reply_text.assert_awaited_once()
    sent = message.reply_text.await_args.args[0]
    assert "Gemini" in sent or "GEMINI_API_KEY" in sent


async def test_no_upcoming_games(app_context: AppContext) -> None:
    ctx = _ctx(_with_generator(app_context, FakeGenerator([])))
    update, message = _update()
    await palpite_handler(update, ctx)
    sent = message.reply_text.await_args.args[0]
    assert "24h" in sent


async def test_cold_cache_generates_and_posts_per_game(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    _seed_game(app_context, 2)
    gen = FakeGenerator([1, 2])
    update, message = _update()

    await palpite_handler(update, _ctx(_with_generator(app_context, gen)))

    assert gen.calls == 1  # generated once
    texts = [c.args[0] for c in message.reply_text.await_args_list]
    # one "working" message + one message per game
    assert any("Analisando" in t for t in texts)
    assert sum("Palpite da IA" in t for t in texts) == 2


async def test_warm_cache_skips_generation(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    palpite_date = datetime.now(app_context.settings.tzinfo).date()
    with app_context.session_factory() as s:
        PalpiteRepository(s).upsert(
            fixture_id=1, palpite_date=palpite_date, payload_json=_palpite_json(1)
        )
        s.commit()
    gen = FakeGenerator([1])
    update, message = _update()

    await palpite_handler(update, _ctx(_with_generator(app_context, gen)))

    assert gen.calls == 0  # cache hit -> no Gemini call, no "working" message
    texts = [c.args[0] for c in message.reply_text.await_args_list]
    assert not any("Analisando" in t for t in texts)
    assert sum("Palpite da IA" in t for t in texts) == 1


async def test_generation_failure_reports_error(app_context: AppContext) -> None:
    _seed_game(app_context, 1)

    class BoomGenerator:
        calls = 0

        async def generate(self, *, system_instruction: str, user_content: str) -> str:
            raise RuntimeError("gemini down")

    update, message = _update()
    await palpite_handler(
        update, _ctx(_with_generator(app_context, cast(FakeGenerator, BoomGenerator())))
    )

    texts = [c.args[0] for c in message.reply_text.await_args_list]
    assert any("não consegui" in t.lower() for t in texts)


async def test_does_not_generate_while_a_generation_is_in_progress(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    gen = FakeGenerator([1])
    actx = _with_generator(app_context, gen)
    await actx.palpite_lock.acquire()  # simulate an in-flight generation
    try:
        update, message = _update()
        await palpite_handler(update, _ctx(actx))
    finally:
        actx.palpite_lock.release()

    assert gen.calls == 0  # must NOT start a second Gemini request
    texts = [c.args[0] for c in message.reply_text.await_args_list]
    assert any("já estou analisando" in t.lower() for t in texts)


async def test_concurrent_cold_cache_generates_only_once(app_context: AppContext) -> None:
    _seed_game(app_context, 1)

    class SlowGenerator:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, *, system_instruction: str, user_content: str) -> str:
            self.calls += 1
            await asyncio.sleep(0.05)  # hold the lock long enough for the 2nd call to arrive
            return '{"palpites": [' + _palpite_json(1) + "]}"

    gen = SlowGenerator()
    actx = _with_generator(app_context, cast(FakeGenerator, gen))  # shared lock across both calls
    u1, _m1 = _update()
    u2, _m2 = _update()

    await asyncio.gather(palpite_handler(u1, _ctx(actx)), palpite_handler(u2, _ctx(actx)))

    assert gen.calls == 1  # exactly one AI request despite two concurrent /palpite
