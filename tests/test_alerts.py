"""Tests for admin alerts (COMPLETION.md §14)."""

from __future__ import annotations

from datetime import date
from typing import cast
from unittest.mock import AsyncMock

from telegram import Bot
from telegram.error import TelegramError

from tigrinho.bot.alerts import alert_cap_reached, notify_admin
from tigrinho.bot.runtime import AppContext


async def test_notify_admin_swallows_delivery_failure() -> None:
    bot = AsyncMock(spec=Bot)
    bot.send_message.side_effect = TelegramError("forbidden")
    await notify_admin(cast(Bot, bot), 999, "hi")  # must not raise


async def test_alert_cap_reached_once_per_day(app_context: AppContext) -> None:
    bot = AsyncMock(spec=Bot)
    day1 = date(2026, 6, 15)
    day2 = date(2026, 6, 16)
    await alert_cap_reached(app_context, cast(Bot, bot), day1)
    await alert_cap_reached(app_context, cast(Bot, bot), day1)  # deduped
    await alert_cap_reached(app_context, cast(Bot, bot), day2)
    assert bot.send_message.await_count == 2
