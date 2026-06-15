"""Provider selection from settings (shared by the bot entrypoint and the CLI; §4)."""

from __future__ import annotations

from tigrinho.config import Settings
from tigrinho.providers.api_football import ApiFootballProvider
from tigrinho.providers.base import FootballProvider
from tigrinho.providers.fake import FakeProvider


def make_provider(settings: Settings) -> FootballProvider:
    """Build the configured football provider (``fake`` for local/dev, else API-Football)."""
    if settings.provider_mode == "fake":
        return FakeProvider()
    return ApiFootballProvider(
        base_url=settings.api_football_base_url,
        api_key=settings.api_football_key,
        league_id=settings.wc_league_id,
        season=settings.wc_season,
    )
