"""Admin DM alerts + the PTB error-handler backstop (COMPLETION.md §14).

Scheduled jobs catch their own exceptions; this module is the last-resort notifier so an operator
learns about failures via a DM and structured logs. Sending a DM is best-effort: if the admin has
not pressed Start, Telegram rejects it, which we log rather than raise.
"""

from __future__ import annotations

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from tigrinho.bot.runtime import get_app_context
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.alerts")


async def notify_admin(bot: Bot, admin_user_id: int, text: str) -> None:
    """DM the admin; best-effort (logs and swallows a Telegram delivery failure)."""
    try:
        await bot.send_message(chat_id=admin_user_id, text=text, parse_mode=ParseMode.HTML)
    except TelegramError as exc:
        _log.warning("admin_dm_failed", admin_user_id=admin_user_id, error=str(exc))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB backstop: log any unhandled handler/job exception and alert the admin (§14)."""
    error = context.error
    _log.error("unhandled_error", error=str(error), error_type=type(error).__name__)
    app_context = get_app_context(context.application)
    await notify_admin(
        context.bot,
        app_context.settings.admin_user_id,
        f"⚠️ Erro não tratado no bot: <code>{type(error).__name__}: {error}</code>",
    )
