"""Telegram message helpers (COMPLETION.md §14).

``safe_edit_text`` edits a callback-query message but treats Telegram's "Message is not modified"
``BadRequest`` as a no-op. That error is raised whenever an edit's text+markup is byte-identical to
the current message (ordinary double-taps, or two players tapping the same group button), and would
otherwise bubble to the global error handler and DM the admin a spurious alert.
"""

from __future__ import annotations

from telegram import CallbackQuery, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

_NOT_MODIFIED = "message is not modified"


async def safe_edit_text(
    query: CallbackQuery, text: str, *, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Edit the message in place (HTML); silently ignore the 'message is not modified' no-op."""
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except BadRequest as exc:
        if _NOT_MODIFIED not in str(exc).lower():
            raise
