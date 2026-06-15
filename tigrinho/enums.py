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
