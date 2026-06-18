"""Tests for the /palpite game picker + per-game palpite callback (COMPLETION.md §20)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from tigrinho.ai.schemas import GamePalpite
from tigrinho.bot.callbacks import CallbackData, PalpiteView, decode, encode
from tigrinho.bot.palpite_handlers import palpite_handler, palpite_select
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


def _seed_game(
    app_context: AppContext,
    fixture_id: int,
    status: GameStatus = GameStatus.SCHEDULED,
    *,
    hours: float = 3,
) -> None:
    kickoff = _now() + timedelta(hours=hours)
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
                status=status,
            )
        )
        session.commit()


def _cache_palpite(app_context: AppContext, fixture_id: int) -> None:
    palpite_date = datetime.now(app_context.settings.tzinfo).date()
    with app_context.session_factory() as session:
        PalpiteRepository(session).upsert(
            fixture_id=fixture_id, palpite_date=palpite_date, payload_json=_palpite_json(fixture_id)
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


def _callback_update(fixture_id: int) -> tuple[Update, AsyncMock]:
    query = AsyncMock()
    query.data = encode(PalpiteView(fixture_id))
    update = MagicMock()
    update.callback_query = query
    return cast(Update, update), query


def _picker_choices(keyboard: InlineKeyboardMarkup) -> list[CallbackData]:
    return [
        decode(button.callback_data)
        for row in keyboard.inline_keyboard
        for button in row
        if isinstance(button.callback_data, str)
    ]


def _picker_labels(keyboard: InlineKeyboardMarkup) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


def _edited_texts(query: AsyncMock) -> list[str]:
    return [c.args[0] for c in query.edit_message_text.await_args_list]


# --- /palpite command: shows the game picker -------------------------------------------------


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


async def test_command_shows_game_picker_without_generating(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    _seed_game(app_context, 2)
    gen = FakeGenerator([1, 2])
    update, message = _update()

    await palpite_handler(update, _ctx(_with_generator(app_context, gen)))

    assert gen.calls == 0  # listing the games must NOT trigger any AI generation
    message.reply_text.assert_awaited_once()
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert _picker_choices(keyboard) == [PalpiteView(1), PalpiteView(2)]


async def test_command_lists_live_games_with_marker(app_context: AppContext) -> None:
    # A running (LIVE) game is offered alongside upcoming ones, labelled as live (§20).
    _seed_game(app_context, 1, GameStatus.LIVE, hours=-1)  # kicked off 1h ago, in progress
    _seed_game(app_context, 2)  # upcoming
    update, message = _update()

    await palpite_handler(update, _ctx(_with_generator(app_context, FakeGenerator([1, 2]))))

    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert _picker_choices(keyboard) == [PalpiteView(1), PalpiteView(2)]  # live first
    labels = _picker_labels(keyboard)
    assert "ao vivo" in labels[0].lower()  # the live game is marked


# --- per-game selection callback -------------------------------------------------------------


async def test_select_no_key_reports_error(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    update, query = _callback_update(1)
    await palpite_select(update, _ctx(app_context))  # no generator
    texts = _edited_texts(query)
    assert any("Gemini" in t or "GEMINI_API_KEY" in t for t in texts)


async def test_select_warm_cache_shows_palpite_without_generating(app_context: AppContext) -> None:
    _seed_game(app_context, 1)
    _cache_palpite(app_context, 1)
    gen = FakeGenerator([1])
    update, query = _callback_update(1)

    await palpite_select(update, _ctx(_with_generator(app_context, gen)))

    assert gen.calls == 0  # cache hit -> no Gemini call, no "working" message
    texts = _edited_texts(query)
    assert not any("Analisando" in t for t in texts)
    assert any("Palpite da IA" in t and "Home1" in t for t in texts)


async def test_select_cold_cache_generates_and_shows_only_the_chosen_game(
    app_context: AppContext,
) -> None:
    _seed_game(app_context, 1)
    _seed_game(app_context, 2)
    gen = FakeGenerator([1, 2])
    update, query = _callback_update(2)

    await palpite_select(update, _ctx(_with_generator(app_context, gen)))

    assert gen.calls == 1  # generated once on demand
    texts = _edited_texts(query)
    assert any("Analisando" in t for t in texts)  # "working" message first
    final = texts[-1]
    assert "Palpite da IA" in final and "Home2" in final  # the chosen game…
    assert "Home1" not in final  # …and only that game


async def test_select_live_game_cold_cache_generates(app_context: AppContext) -> None:
    # Tapping a running (LIVE) game with a cold cache generates its palpite on demand (§20).
    _seed_game(app_context, 1, GameStatus.LIVE, hours=-1)
    gen = FakeGenerator([1])
    update, query = _callback_update(1)

    await palpite_select(update, _ctx(_with_generator(app_context, gen)))

    assert gen.calls == 1
    final = _edited_texts(query)[-1]
    assert "Palpite da IA" in final and "Home1" in final


async def test_select_generation_failure_reports_error(app_context: AppContext) -> None:
    _seed_game(app_context, 1)

    class BoomGenerator:
        calls = 0

        async def generate(self, *, system_instruction: str, user_content: str) -> str:
            raise RuntimeError("gemini down")

    update, query = _callback_update(1)
    await palpite_select(
        update, _ctx(_with_generator(app_context, cast(FakeGenerator, BoomGenerator())))
    )

    texts = _edited_texts(query)
    assert any("não consegui" in t.lower() for t in texts)


async def test_select_does_not_generate_while_a_generation_is_in_progress(
    app_context: AppContext,
) -> None:
    _seed_game(app_context, 1)
    gen = FakeGenerator([1])
    actx = _with_generator(app_context, gen)
    await actx.palpite_lock.acquire()  # simulate an in-flight generation
    try:
        update, query = _callback_update(1)
        await palpite_select(update, _ctx(actx))
    finally:
        actx.palpite_lock.release()

    assert gen.calls == 0  # must NOT start a second Gemini request
    texts = _edited_texts(query)
    assert any("já estou analisando" in t.lower() for t in texts)


async def test_select_incomplete_generation_does_not_regenerate_every_call(
    app_context: AppContext,
) -> None:
    # If the model omits the chosen fixture, that gap must not re-trigger a full (slow, budgeted)
    # Gemini batch on every tap — §20.1: a day's predictions are computed once.
    _seed_game(app_context, 1)
    _seed_game(app_context, 2)
    gen = FakeGenerator([1])  # model only ever returns fixture 1, always omits fixture 2
    actx = _with_generator(app_context, gen)

    u1, _q1 = _callback_update(2)
    await palpite_select(u1, _ctx(actx))
    assert gen.calls == 1  # first tap generates

    u2, _q2 = _callback_update(2)
    await palpite_select(u2, _ctx(actx))
    assert gen.calls == 1  # second tap must NOT regenerate the still-missing fixture


async def test_select_concurrent_cold_cache_generates_only_once(app_context: AppContext) -> None:
    _seed_game(app_context, 1)

    class SlowGenerator:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, *, system_instruction: str, user_content: str) -> str:
            self.calls += 1
            await asyncio.sleep(0.05)  # hold the lock long enough for the 2nd tap to arrive
            return '{"palpites": [' + _palpite_json(1) + "]}"

    gen = SlowGenerator()
    actx = _with_generator(app_context, cast(FakeGenerator, gen))  # shared lock across both taps
    u1, _q1 = _callback_update(1)
    u2, _q2 = _callback_update(1)

    await asyncio.gather(palpite_select(u1, _ctx(actx)), palpite_select(u2, _ctx(actx)))

    assert gen.calls == 1  # exactly one AI request despite two concurrent taps
