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

from tigrinho.ai.base import GameScorer, PalpiteGenerator
from tigrinho.config import Settings
from tigrinho.providers.base import FootballProvider
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.splitwise import SplitwiseClient

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
    # Daily-bolãozinho game-interest scorer (§24); None when the feature is disabled.
    game_scorer: GameScorer | None = None
    # Serializes AI palpite generation so concurrent /palpite calls don't fire duplicate Gemini
    # requests when the cache is cold (§20).
    palpite_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # (palpite_date, fixture_id) pairs already attempted this process, so a fixture the model
    # omitted from its batch isn't re-requested on every /palpite (§20.1 "computed at most once").
    palpite_attempted: set[tuple[date, int]] = field(default_factory=set)
    # Budget days for which the "cap reached" admin alert was already sent (dedup, once/day, §14).
    alerted_cap_days: set[date] = field(default_factory=set)
    # Fixture ids already alerted as "stuck" this process, so the poll job DMs the admin once per
    # stuck game rather than every cycle; pruned when a game stops being stuck so it can re-alert.
    stuck_alerted: set[int] = field(default_factory=set)
    # Per-game count of group "Placar corrigido" posts made by the reconcile job this process, so a
    # flapping (VAR-oscillating) feed cannot spam the group; capped, then it DMs the admin (§8.3).
    reconcile_posts: dict[int, int] = field(default_factory=dict)
    # Per-bolãozinho count of group result *correction* posts this process, capped like the
    # reconcile posts so an oscillating re-grade can't spam contradictory winners (§22/§7).
    tournament_corrections: dict[int, int] = field(default_factory=dict)
    # Bolãozinho ids already alerted as "stuck" this process (a member game stranded past its
    # window), so the sweep DMs the admin once per stranded bolãozinho, not every cycle (§22/§7).
    tournament_stuck_alerted: set[int] = field(default_factory=set)
    # Splitwise client (§23); None when the feature is disabled (no key/group configured).
    splitwise_client: SplitwiseClient | None = None
    # Per-bolãozinho count of Splitwise expense *correction* updates this process, capped like the
    # group correction posts so an oscillating re-grade can't thrash the ledger (§23).
    splitwise_corrections: dict[int, int] = field(default_factory=dict)


def get_app_context(application: AnyApplication) -> AppContext:
    """Fetch the AppContext stored in ``application.bot_data`` (raises if not initialized)."""
    context = application.bot_data.get(APP_CONTEXT_KEY)
    if not isinstance(context, AppContext):
        raise RuntimeError("AppContext is not initialized in application.bot_data")
    return context
