"""Tests for executing a Splitwise registration from the bot layer (§23)."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock

import httpx
from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import ContextTypes

import tigrinho.tournament_service as tsvc
from tigrinho.bot.runtime import AppContext
from tigrinho.bot.splitwise_register import register_finished_tournament, register_tournament
from tigrinho.config import Settings
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import BetRepository, PlayerRepository, TournamentRepository
from tigrinho.providers.splitwise import SplitwiseClient, SplitwiseError

_NOW = datetime(2026, 6, 16, 12, 0)


def _seed_finished(session: Session, *, auto: bool, link: bool = True) -> int:
    PlayerRepository(session).get_or_create(100, "Ana")
    PlayerRepository(session).get_or_create(200, "Bruno")
    for fid in (1,):
        session.add(
            Game(
                fixture_id=fid,
                match_hash=f"h{fid}",
                stage=Stage.GROUP,
                home_team_id=10,
                home_team_name="Brasil",
                away_team_id=20,
                away_team_name="Argentina",
                kickoff_utc=datetime(2026, 6, 16, 19, 0),
                kickoff_local=datetime(2026, 6, 16, 19, 0),
                status=GameStatus.SCHEDULED,
            )
        )
    session.flush()
    t = tsvc.create_tournament(session, name="Fase", entry_price_cents=1000, created_by=1)
    tsvc.add_game(session, t, 1, now=_NOW)
    tsvc.open_tournament(session, t, now=_NOW, splitwise_enabled=auto)
    if link:
        for tid, uid in ((100, 1001), (200, 1002)):
            p = PlayerRepository(session).get(tid)
            assert p is not None
            p.splitwise_user_id = uid
    tsvc.join(session, t, telegram_id=100, display_name="Ana", now=_NOW)
    tsvc.join(session, t, telegram_id=200, display_name="Bruno", now=_NOW)
    for player, points in ((100, 5), (200, 2)):
        bet = BetRepository(session).upsert(
            fixture_id=1, player_telegram_id=player, category="WINNER", payload_json="{}"
        )
        bet.is_correct = points > 0
        bet.points_awarded = points
        bet.settled_at = datetime(2026, 6, 16, 21, 0)
    game = session.get(Game, 1)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.settled_at = datetime(2026, 6, 16, 21, 0)
    session.flush()
    return t.id


def _enabled_context(
    settings: Settings, session_factory: sessionmaker[Session], client: object
) -> tuple[AppContext, object]:
    base = AppContext(
        settings=settings.model_copy(update={"splitwise_api_key": "k", "splitwise_group_id": 55}),
        provider=AsyncMock(),
        session_factory=session_factory,
        budget=AsyncMock(),
        splitwise_client=cast(SplitwiseClient, client),
    )
    ctx = AsyncMock()
    return base, ctx


async def test_register_tournament_creates_and_persists(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.create_expense.return_value = 777
    app_context, _ = _enabled_context(settings, session_factory, client)

    changed = await register_tournament(app_context, tid)
    assert changed is True
    client.create_expense.assert_awaited_once()
    # cost = one loser × R$10 = 1000 cents
    kwargs = client.create_expense.await_args.kwargs
    assert kwargs["cost_cents"] == 1000
    with session_factory() as session:
        t = TournamentRepository(session).get(tid)
        assert t is not None
        assert t.splitwise_expense_id == 777
        assert t.splitwise_synced_signature is not None


async def test_register_tournament_noop_when_already_synced(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.create_expense.return_value = 777
    app_context, _ = _enabled_context(settings, session_factory, client)

    assert await register_tournament(app_context, tid) is True
    client.create_expense.reset_mock()
    # Second run: signature already synced → nothing to do.
    assert await register_tournament(app_context, tid) is False
    client.create_expense.assert_not_awaited()


async def test_register_finished_skips_manual(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=False)  # MANUAL
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    app_context, ctx = _enabled_context(settings, session_factory, client)
    await register_finished_tournament(
        app_context, cast(ContextTypes.DEFAULT_TYPE, ctx), tid, is_correction=False
    )
    client.create_expense.assert_not_awaited()


async def test_register_finished_auto_registers(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.create_expense.return_value = 777
    app_context, ctx = _enabled_context(settings, session_factory, client)
    await register_finished_tournament(
        app_context, cast(ContextTypes.DEFAULT_TYPE, ctx), tid, is_correction=False
    )
    client.create_expense.assert_awaited_once()


async def test_register_finished_disabled_is_noop(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        session.commit()
    # Feature disabled (no client / default settings).
    app_context = AppContext(
        settings=settings,
        provider=AsyncMock(),
        session_factory=session_factory,
        budget=AsyncMock(),
        splitwise_client=None,
    )
    ctx = AsyncMock()
    await register_finished_tournament(
        app_context, cast(ContextTypes.DEFAULT_TYPE, ctx), tid, is_correction=False
    )  # no crash, no call


async def test_register_finished_api_error_dms_admin(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.create_expense.side_effect = SplitwiseError("boom")
    app_context, ctx = _enabled_context(settings, session_factory, client)
    await register_finished_tournament(
        app_context, cast(ContextTypes.DEFAULT_TYPE, ctx), tid, is_correction=False
    )
    cast(AsyncMock, ctx).bot.send_message.assert_awaited()  # admin notified, no crash


async def test_register_finished_correction_cap(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        t = TournamentRepository(session).get(tid)
        assert t is not None
        t.splitwise_expense_id = 555  # already registered → an update is a correction
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    app_context, ctx = _enabled_context(settings, session_factory, client)
    app_context.splitwise_corrections[tid] = 2  # cap already reached
    await register_finished_tournament(
        app_context, cast(ContextTypes.DEFAULT_TYPE, ctx), tid, is_correction=True
    )
    client.update_expense.assert_not_awaited()  # capped
    cast(AsyncMock, ctx).bot.send_message.assert_awaited()  # admin told once


async def test_register_handles_httpx_error(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        tid = _seed_finished(session, auto=True)
        session.commit()
    client = AsyncMock(spec=SplitwiseClient)
    client.create_expense.side_effect = httpx.ConnectError("down")
    app_context, ctx = _enabled_context(settings, session_factory, client)
    await register_finished_tournament(
        app_context, cast(ContextTypes.DEFAULT_TYPE, ctx), tid, is_correction=False
    )
    cast(AsyncMock, ctx).bot.send_message.assert_awaited()
