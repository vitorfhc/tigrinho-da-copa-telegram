"""Bot entrypoint: ``python -m tigrinho`` (COMPLETION.md §15).

Wires config → engine/session factory → provider → request budget → PTB application, then runs
long polling. Database migrations are applied by the container entrypoint (``alembic upgrade head``)
before this module starts.
"""

from __future__ import annotations

from tigrinho.ai.base import PalpiteGenerator
from tigrinho.ai.gemini import GeminiPalpiteGenerator
from tigrinho.bot.app import build_application
from tigrinho.bot.runtime import AnyApplication, AppContext
from tigrinho.config import Settings, get_settings
from tigrinho.db.engine import create_db_engine, create_session_factory
from tigrinho.logging import configure_logging, get_logger
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.factory import make_provider

_log = get_logger("tigrinho.main")


def make_palpite_generator(settings: Settings) -> PalpiteGenerator | None:
    """Build the Gemini palpite generator, or None when no key is configured (§20)."""
    if not settings.gemini_api_key:
        return None
    return GeminiPalpiteGenerator(api_key=settings.gemini_api_key, model=settings.gemini_model)


def create_application_from_settings(settings: Settings) -> AnyApplication:
    """Build the fully wired PTB application from validated settings (no network at build time)."""
    engine = create_db_engine(settings.db_path)
    session_factory = create_session_factory(engine)
    app_context = AppContext(
        settings=settings,
        provider=make_provider(settings),
        session_factory=session_factory,
        budget=RequestBudget(
            session_factory,
            daily_cap=settings.api_daily_cap,
            reset_tz=settings.budget_tzinfo,
        ),
        palpite_generator=make_palpite_generator(settings),
    )
    return build_application(app_context)


def main() -> None:
    """Load+validate config, build the bot, and run long polling (blocks)."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    _log.info(
        "starting",
        provider_mode=settings.provider_mode,
        group_chat_id=settings.group_chat_id,
        db_path=settings.db_path,
    )
    application = create_application_from_settings(settings)
    application.run_polling()


if __name__ == "__main__":  # pragma: no cover
    main()
