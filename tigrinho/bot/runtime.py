"""Shared bot runtime wiring (COMPLETION.md §5).

``AppContext`` bundles the dependencies every handler/job needs (settings, provider, DB session
factory, request budget). It is stored in ``application.bot_data`` so handlers can retrieve it
without import cycles.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import Application

from tigrinho.ai.base import PalpiteGenerator
from tigrinho.config import Settings
from tigrinho.providers.base import FootballProvider
from tigrinho.providers.budget import RequestBudget

# A fully Any-parameterized Application — handlers are typed separately via the default context,
# so the bot-wiring layer does not need the exact 6-parameter Application generic.
AnyApplication = Application[Any, Any, Any, Any, Any, Any]

APP_CONTEXT_KEY: Final = "app_context"


@dataclass(frozen=True, slots=True)
class AppContext:
    """Dependencies shared across handlers and scheduled jobs."""

    settings: Settings
    provider: FootballProvider
    session_factory: sessionmaker[Session]
    budget: RequestBudget
    # AI palpite generator (§20); None when no GEMINI_API_KEY is configured (feature disabled).
    palpite_generator: PalpiteGenerator | None = None
    # Serializes AI palpite generation so concurrent /palpite calls don't fire duplicate Gemini
    # requests when the cache is cold (§20).
    palpite_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Budget days for which the "cap reached" admin alert was already sent (dedup, once/day, §14).
    alerted_cap_days: set[date] = field(default_factory=set)
    # Fixture ids already alerted as "stuck" this process, so the poll job DMs the admin once per
    # stuck game rather than every cycle; pruned when a game stops being stuck so it can re-alert.
    stuck_alerted: set[int] = field(default_factory=set)


def get_app_context(application: AnyApplication) -> AppContext:
    """Fetch the AppContext stored in ``application.bot_data`` (raises if not initialized)."""
    context = application.bot_data.get(APP_CONTEXT_KEY)
    if not isinstance(context, AppContext):
        raise RuntimeError("AppContext is not initialized in application.bot_data")
    return context
