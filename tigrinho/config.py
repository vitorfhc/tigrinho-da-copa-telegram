"""Application configuration (COMPLETION.md §4).

Secrets (``TELEGRAM_BOT_TOKEN``, ``API_FOOTBALL_KEY``) come from the environment / ``.env``.
Every other setting comes from ``config.yaml`` (path taken from ``CONFIG_PATH``, default
``./config.yaml``), loaded through pydantic-settings' ``YamlConfigSettingsSource``. The two
source sets are disjoint; on collision the environment wins. Everything is validated at startup
so the bot fails fast on anything missing or malformed.

Grounding (per §2), verified June 2026:
- pydantic-settings 2.14.x — https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/
  Override ``settings_customise_sources`` and place ``YamlConfigSettingsSource(settings_cls,
  yaml_file=...)`` in the returned tuple. The first source in the tuple wins on conflicts. YAML
  support requires the ``pydantic-settings[yaml]`` extra (PyYAML). A missing ``yaml_file`` is
  skipped (the file source returns ``{}``), so required values must then come from the environment.
"""

from __future__ import annotations

import os
from datetime import time
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

CONFIG_PATH_ENV = "CONFIG_PATH"
DEFAULT_CONFIG_PATH = "./config.yaml"

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"]


class Settings(BaseSettings):
    """Single, validated settings object assembled from ``.env`` + ``config.yaml``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Secrets: environment / .env (§4.1) -------------------------------------------------
    telegram_bot_token: str = Field(min_length=1)
    api_football_key: str = Field(min_length=1)

    # --- Settings: config.yaml (§4.2) -------------------------------------------------------
    group_chat_id: int
    admin_user_id: int
    bot_username: str = Field(min_length=1)

    provider_mode: Literal["api_football", "fake"] = "api_football"
    api_football_base_url: str = "https://v3.football.api-sports.io"
    wc_league_id: int = 1
    wc_season: int = 2026
    timezone: str = "America/Sao_Paulo"
    sync_time: str = "06:00"
    poll_interval_minutes: int = Field(default=10, gt=0)
    match_window_hours: int = Field(default=3, gt=0)
    api_daily_cap: int = Field(default=100, gt=0)
    api_budget_reset_tz: str = "UTC"
    db_path: str = "/data/tigrinho.db"
    log_level: LogLevel = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("bot_username")
    @classmethod
    def _strip_at(cls, value: str) -> str:
        return value.lstrip("@")

    @field_validator("timezone", "api_budget_reset_tz")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"invalid timezone: {value!r}") from exc
        return value

    @field_validator("sync_time")
    @classmethod
    def _valid_sync_time(cls, value: str) -> str:
        try:
            hours, minutes = value.split(":")
            time(int(hours), int(minutes))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid sync_time, expected HH:MM: {value!r}") from exc
        return value

    @property
    def sync_time_obj(self) -> time:
        """The configured daily sync time as a ``datetime.time`` (local to ``timezone``)."""
        hours, minutes = self.sync_time.split(":")
        return time(int(hours), int(minutes))

    @property
    def tzinfo(self) -> ZoneInfo:
        """Display / scheduling timezone."""
        return ZoneInfo(self.timezone)

    @property
    def budget_tzinfo(self) -> ZoneInfo:
        """Timezone whose midnight resets the API request budget."""
        return ZoneInfo(self.api_budget_reset_tz)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path = os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=config_path)
        # First source wins: env > .env > config.yaml. Secrets in env, all other settings in YAML.
        return (init_settings, env_settings, dotenv_settings, yaml_source, file_secret_settings)


@lru_cache
def get_settings() -> Settings:
    """Load, validate and cache the process-wide settings (fail-fast on first call)."""
    return Settings()
