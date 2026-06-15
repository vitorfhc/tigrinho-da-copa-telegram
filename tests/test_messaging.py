"""Tests for safe_edit_text (COMPLETION.md §14 — tolerate 'message is not modified')."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from telegram import CallbackQuery
from telegram.error import BadRequest

from tigrinho.bot.messaging import safe_edit_text


async def test_swallows_message_not_modified() -> None:
    query = AsyncMock(spec=CallbackQuery)
    query.edit_message_text.side_effect = BadRequest("Message is not modified: ...")
    await safe_edit_text(cast(CallbackQuery, query), "same text")  # must not raise


async def test_reraises_other_bad_request() -> None:
    query = AsyncMock(spec=CallbackQuery)
    query.edit_message_text.side_effect = BadRequest("Chat not found")
    with pytest.raises(BadRequest):
        await safe_edit_text(cast(CallbackQuery, query), "x")


async def test_passes_through_on_success() -> None:
    query = AsyncMock(spec=CallbackQuery)
    await safe_edit_text(cast(CallbackQuery, query), "hello")
    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.await_args.args[0] == "hello"
