"""Admin DM alerts + the PTB error-handler backstop (COMPLETION.md §14).

Scheduled jobs catch their own exceptions; this module is the last-resort notifier so an operator
learns about failures via a DM and structured logs. Sending a DM is best-effort: if the admin has
not pressed Start, Telegram rejects it, which we log rather than raise.
"""

from __future__ import annotations

from datetime import date

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import ContextTypes

from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.alerts")


async def notify_admin(bot: Bot, admin_user_id: int, text: str) -> None:
    """DM the admin; best-effort (logs and swallows a Telegram delivery failure)."""
    try:
        await bot.send_message(chat_id=admin_user_id, text=text, parse_mode=ParseMode.HTML)
    except TelegramError as exc:
        _log.warning("admin_dm_failed", admin_user_id=admin_user_id, error=str(exc))


async def alert_cap_reached(app_context: AppContext, bot: Bot, budget_date: date) -> None:
    """DM the admin that the daily API cap is reached — at most once per budget day (§14)."""
    if budget_date in app_context.alerted_cap_days:
        return
    app_context.alerted_cap_days.add(budget_date)
    _log.warning("api_cap_reached", budget_date=budget_date.isoformat())
    await notify_admin(
        bot,
        app_context.settings.admin_user_id,
        f"🚧 Limite diário de chamadas à API atingido em {budget_date.isoformat()}. "
        "Polling pausado até o reset.",
    )


def _is_transient_network_error(error: BaseException | None) -> bool:
    """A transient, self-healing connectivity blip that should NOT spam the admin (§14).

    PTB wraps non-timeout ``httpx`` failures (e.g. ``ReadError``/``ConnectError``) and pool/read
    timeouts as ``NetworkError``/``TimedOut`` (a ``NetworkError`` subclass). These recover on the
    next attempt or polling cycle, so they are not actionable. ``BadRequest`` also subclasses
    ``NetworkError`` but is a genuine, un-retryable error — exclude it so it still alerts.
    """
    return isinstance(error, NetworkError) and not isinstance(error, BadRequest)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB backstop: log any unhandled handler/job exception and alert the admin (§14).

    Transient network errors are logged at warning level but not DMed — the admin alert is reserved
    for actionable failures (§14), and a connectivity blip self-heals on retry.
    """
    error = context.error
    if _is_transient_network_error(error):
        _log.warning("transient_network_error", error=str(error), error_type=type(error).__name__)
        return
    _log.error("unhandled_error", error=str(error), error_type=type(error).__name__)
    app_context = get_app_context(context.application)
    await notify_admin(
        context.bot,
        app_context.settings.admin_user_id,
        f"⚠️ Erro não tratado no bot: <code>{type(error).__name__}: {error}</code>",
    )
