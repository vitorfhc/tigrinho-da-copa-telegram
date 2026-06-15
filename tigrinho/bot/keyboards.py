"""Inline keyboard builders (COMPLETION.md §3, §8.2).

Started at M5 for the announcement deep-link buttons; M6 adds the wizard keyboards (games,
categories, score pad, paginated squad, board toggle).
"""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def deep_link_url(bot_username: str, fixture_id: int) -> str:
    """Deep link into the DM betting wizard for a fixture (§8.2)."""
    return f"https://t.me/{bot_username}?start=bet_{fixture_id}"


def announcement_keyboard(
    games: Sequence[tuple[int, str]], bot_username: str
) -> InlineKeyboardMarkup:
    """One 🎯 Apostar URL button per open game. Each item: (fixture_id, label)."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎯 Apostar: {label}",
                url=deep_link_url(bot_username, fixture_id),
            )
        ]
        for fixture_id, label in games
    ]
    return InlineKeyboardMarkup(rows)
