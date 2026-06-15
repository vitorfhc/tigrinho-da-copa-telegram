"""Tests for the bot entrypoint wiring (COMPLETION.md §15)."""

from __future__ import annotations

from tigrinho.__main__ import create_application_from_settings
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.config import Settings


def test_create_application_from_settings(settings: Settings) -> None:
    application = create_application_from_settings(settings)
    app_context = application.bot_data[APP_CONTEXT_KEY]
    assert isinstance(app_context, AppContext)
    assert app_context.settings is settings
    # commands + error handler registered (no network performed at build time)
    assert len(application.handlers[0]) >= 5
    assert len(application.error_handlers) == 1
