"""Tests for inline keyboard builders (COMPLETION.md §8.2)."""

from __future__ import annotations

from tigrinho.bot.keyboards import announcement_keyboard, deep_link_url


def test_deep_link_url() -> None:
    assert deep_link_url("TigrinhoDaCopaBot", 123) == "https://t.me/TigrinhoDaCopaBot?start=bet_123"


def test_announcement_keyboard_one_button_per_game() -> None:
    keyboard = announcement_keyboard(
        [(1, "Brasil x Argentina"), (2, "França x Alemanha")], "TigrinhoDaCopaBot"
    )
    assert len(keyboard.inline_keyboard) == 2
    button = keyboard.inline_keyboard[0][0]
    assert "Apostar" in button.text
    assert button.url == "https://t.me/TigrinhoDaCopaBot?start=bet_1"
