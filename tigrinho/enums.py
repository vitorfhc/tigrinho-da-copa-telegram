"""Shared leaf enums used across the provider, domain and persistence layers.

These have **no I/O and no dependencies** (not even SQLAlchemy), so the pure domain
(`domain/scoring.py`) and the provider value objects can import them without coupling to the
database. ``db/models.py`` re-uses them for its ``Enum`` columns.
"""

from __future__ import annotations

import enum


class Stage(enum.StrEnum):
    """Tournament stage of a fixture (drives the knockout winner rule, §8.1)."""

    GROUP = "GROUP"
    KNOCKOUT = "KNOCKOUT"


class GameStatus(enum.StrEnum):
    """Normalized provider status (§7.2)."""

    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    VOID = "VOID"


class TournamentStatus(enum.StrEnum):
    """Lifecycle of a bolãozinho (Feature 7 / §22)."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class SplitwiseMode(enum.StrEnum):
    """How Splitwise applies to a bolãozinho (Feature 8 / §23).

    ``AUTO`` — opened while the feature was enabled, so the join guard ensured every entrant is
    linked: the result auto-registers (and auto-corrects) in Splitwise. ``MANUAL`` — an old
    bolãozinho: the bot only notifies the admin once it is fully linked and the admin triggers
    registration. ``EXCLUDED`` — never touched (closed at deploy, or already settled by hand).
    """

    AUTO = "AUTO"
    MANUAL = "MANUAL"
    EXCLUDED = "EXCLUDED"
