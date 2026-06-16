"""Tests for the bot skeleton: startup validation, commands, handlers, error backstop (§M4, §16)."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import (
    Bot,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    Update,
    User,
)
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

from tigrinho.bot.alerts import error_handler
from tigrinho.bot.app import (
    GROUP_COMMANDS,
    PRIVATE_COMMANDS,
    StartupError,
    build_application,
    post_init,
    set_commands,
    validate_startup,
)
from tigrinho.bot.help_handlers import cmd_ajuda
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AnyApplication, AppContext
from tigrinho.config import Settings


def _bot(username: str = "TigrinhoDaCopaBot") -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.get_me.return_value = User(id=1, is_bot=True, first_name="Tigrinho", username=username)
    bot.get_chat.return_value = MagicMock()
    return bot


# --- startup validation ---------------------------------------------------------------------


async def test_validate_startup_ok(settings: Settings) -> None:
    bot = _bot(settings.bot_username)
    await validate_startup(cast(Bot, bot), settings)
    assert bot.get_chat.await_count == 2  # group + admin


async def test_validate_startup_username_mismatch(settings: Settings) -> None:
    bot = _bot("SomeOtherBot")
    with pytest.raises(StartupError, match="bot_username mismatch"):
        await validate_startup(cast(Bot, bot), settings)


async def test_validate_startup_group_unreachable(settings: Settings) -> None:
    bot = _bot(settings.bot_username)
    bot.get_chat.side_effect = TelegramError("chat not found")
    with pytest.raises(StartupError, match="cannot reach group_chat_id"):
        await validate_startup(cast(Bot, bot), settings)


async def test_validate_startup_admin_unreachable_is_best_effort(settings: Settings) -> None:
    bot = _bot(settings.bot_username)
    # group ok, admin DM unreachable -> warn, do NOT raise
    bot.get_chat.side_effect = [MagicMock(), TelegramError("forbidden")]
    await validate_startup(cast(Bot, bot), settings)


# --- commands -------------------------------------------------------------------------------


async def test_set_commands_uses_scopes() -> None:
    bot = AsyncMock(spec=Bot)
    await set_commands(cast(Bot, bot))
    calls = bot.set_my_commands.await_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == PRIVATE_COMMANDS
    assert isinstance(calls[0].kwargs["scope"], BotCommandScopeAllPrivateChats)
    assert calls[1].args[0] == GROUP_COMMANDS
    assert isinstance(calls[1].kwargs["scope"], BotCommandScopeAllGroupChats)


# --- build_application ----------------------------------------------------------------------


def test_build_application_registers_handlers(app_context: AppContext) -> None:
    application = build_application(app_context)
    assert application.bot_data[APP_CONTEXT_KEY] is app_context
    command_names: set[str] = set()
    for handler in application.handlers[0]:
        if isinstance(handler, CommandHandler):
            command_names |= set(handler.commands)
    assert {"start", "ajuda", "apostar", "minhas_apostas", "jogos", "placar_jogos"} <= command_names
    assert len(application.error_handlers) == 1


# --- help handlers --------------------------------------------------------------------------


async def test_cmd_ajuda_replies_with_help() -> None:
    update = MagicMock()
    update.effective_message = AsyncMock()
    context = MagicMock()
    await cmd_ajuda(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Tigrinho da Copa" in text


# --- error handler --------------------------------------------------------------------------


async def test_error_handler_notifies_admin(app_context: AppContext) -> None:
    application = build_application(app_context)
    context = MagicMock()
    context.error = ValueError("boom")
    context.application = application
    context.bot = AsyncMock(spec=Bot)
    await error_handler(object(), cast(ContextTypes.DEFAULT_TYPE, context))
    context.bot.send_message.assert_awaited_once()
    assert (
        context.bot.send_message.await_args.kwargs["chat_id"] == app_context.settings.admin_user_id
    )


# --- post_init job scheduling ---------------------------------------------------------------


async def test_post_init_schedules_all_jobs(app_context: AppContext) -> None:
    application = MagicMock()
    application.bot_data = {APP_CONTEXT_KEY: app_context}
    application.bot = _bot(app_context.settings.bot_username)
    application.job_queue = MagicMock()

    # post_init runs validate_startup + set_commands before scheduling; _bot() satisfies both.
    with (
        patch("tigrinho.bot.app.schedule_sync_job") as sync_mock,
        patch("tigrinho.bot.app.schedule_poll_job") as poll_mock,
        patch("tigrinho.bot.app.schedule_reminder_job") as reminder_mock,
        patch("tigrinho.bot.app.schedule_reconcile_job") as reconcile_mock,
    ):
        await post_init(cast(AnyApplication, application))

    sync_mock.assert_called_once_with(application.job_queue, app_context.settings)
    poll_mock.assert_called_once_with(application.job_queue, app_context.settings)
    reminder_mock.assert_called_once_with(application.job_queue, app_context.settings)
    reconcile_mock.assert_called_once_with(application.job_queue, app_context.settings)
