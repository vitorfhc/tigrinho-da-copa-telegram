"""Structured logging setup (COMPLETION.md §14).

structlog configured to emit one JSON object per line (production) or a colourised console
render (local), to stdout, so logs are visible via ``docker compose logs``.

Grounding (per §2), verified June 2026:
- structlog 25/26.x — https://www.structlog.org/en/stable/
  ``structlog.configure(processors=[...], wrapper_class=make_filtering_bound_logger(level),
  logger_factory=PrintLoggerFactory())``; ``processors.JSONRenderer`` vs ``dev.ConsoleRenderer``.
"""

from __future__ import annotations

import logging
from typing import Literal, cast

import structlog
from structlog.typing import FilteringBoundLogger

LogFormat = Literal["json", "console"]


def configure_logging(level: str = "INFO", fmt: LogFormat = "json") -> None:
    """Configure the global structlog pipeline. Idempotent; safe to call again to reconfigure."""
    level_no = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if fmt == "json" else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Return a structlog logger bound to ``name``."""
    return cast(FilteringBoundLogger, structlog.get_logger(name))
