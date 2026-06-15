"""`/ajuda` handler (COMPLETION.md §11).

`/start` (welcome + the deep-link `bet_<fixture_id>` payload) lives in ``bets_handlers.py``.
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from tigrinho.domain.text_pt import help_text


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(help_text(), parse_mode=ParseMode.HTML)
