"""Tests for posting bolãozinho announcements to the group (Feature 7 / §22, §22.4)."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ContextTypes

from tigrinho.bot.runtime import AppContext
from tigrinho.bot.tournament_announce import post_tournament_announcements
from tigrinho.tournament_service import TournamentPartialAnnouncement


def _context() -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


async def test_partial_announcement_posts_standings_to_group(app_context: AppContext) -> None:
    ann = TournamentPartialAnnouncement(
        tournament_id=1,
        name="Oitavas",
        settled_count=1,
        total_games=3,
        n_entrants=2,
        pot_cents=2000,
        prize_cents=1000,
        standings=(("Ana", 5), ("Bruno", 2)),
    )
    context = _context()
    await post_tournament_announcements(app_context, context, [ann])
    context.bot.send_message.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = context.bot.send_message.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["chat_id"] == app_context.settings.group_chat_id
    assert "Placar parcial" in kwargs["text"]
    assert "1/3 jogos" in kwargs["text"]
    assert "🥇 Ana" in kwargs["text"]
