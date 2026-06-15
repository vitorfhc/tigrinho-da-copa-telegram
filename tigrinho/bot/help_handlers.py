"""`/ajuda` and `/start` (no-payload welcome) handlers (COMPLETION.md §11).

The deep-link `/start bet_<fixture_id>` payload is handled in ``bets_handlers.py`` (M6); here
``/start`` with no payload just shows the welcome.
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from tigrinho.domain.text_pt import help_text, welcome_text


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(help_text(), parse_mode=ParseMode.HTML)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    # M6 extends this to parse a `bet_<fixture_id>` deep-link payload (context.args).
    await message.reply_text(welcome_text(), parse_mode=ParseMode.HTML)
