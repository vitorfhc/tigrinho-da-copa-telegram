"""Tests for the configuration layer (COMPLETION.md §4, §16)."""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tigrinho.config import CONFIG_PATH_ENV, Settings, get_settings

# Every settings field name (uppercased) — cleared before each build so a stray host env var
# cannot leak into the loaded Settings and make tests non-deterministic.
_FIELD_ENV_NAMES = [
    "TELEGRAM_BOT_TOKEN",
    "API_FOOTBALL_KEY",
    "GROUP_CHAT_ID",
    "ADMIN_USER_ID",
    "BOT_USERNAME",
    "PROVIDER_MODE",
    "API_FOOTBALL_BASE_URL",
    "WC_LEAGUE_ID",
    "WC_SEASON",
    "TIMEZONE",
    "SYNC_TIME",
    "POLL_INTERVAL_MINUTES",
    "REMINDER_LEAD_MINUTES",
    "REMINDER_INTERVAL_MINUTES",
    "MATCH_WINDOW_HOURS",
    "API_DAILY_CAP",
    "API_BUDGET_RESET_TZ",
    "DB_PATH",
    "LOG_LEVEL",
    "LOG_FORMAT",
]

VALID_YAML = """\
group_chat_id: -1001234567890
admin_user_id: 123456789
bot_username: TigrinhoDaCopaBot
provider_mode: fake
timezone: America/Sao_Paulo
sync_time: "06:00"
"""


def _build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    yaml_content: str | None = VALID_YAML,
    env: dict[str, str] | None = None,
    set_secrets: bool = True,
) -> Settings:
    """Construct a Settings from a temp config.yaml + a controlled environment."""
    # chdir into tmp_path so the relative ".env" (model_config) resolves to a non-existent
    # file: tests must never read a developer's real .env.
    monkeypatch.chdir(tmp_path)
    for name in _FIELD_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    if yaml_content is not None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml_content, encoding="utf-8")
        monkeypatch.setenv(CONFIG_PATH_ENV, str(cfg))
    else:
        monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.yaml"))

    if set_secrets:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-123")
        monkeypatch.setenv("API_FOOTBALL_KEY", "key-123")

    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)

    return Settings()


def test_loads_valid_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.group_chat_id == -1001234567890
    assert settings.admin_user_id == 123456789
    assert settings.bot_username == "TigrinhoDaCopaBot"
    assert settings.provider_mode == "fake"


def test_secrets_come_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.telegram_bot_token == "tok-123"
    assert settings.api_football_key == "key-123"


def test_defaults_applied_for_omitted_optionals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.api_daily_cap == 100
    assert settings.wc_season == 2026
    assert settings.wc_league_id == 1
    assert settings.poll_interval_minutes == 10
    assert settings.match_window_hours == 3
    assert settings.api_budget_reset_tz == "UTC"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.api_football_base_url == "https://v3.football.api-sports.io"


def test_missing_secret_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, set_secrets=False)


def test_missing_required_setting_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    yaml_content = "admin_user_id: 1\nbot_username: Bot\n"  # group_chat_id missing
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, yaml_content=yaml_content)


def test_missing_config_file_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No YAML file at all: required settings unavailable -> fail fast.
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, yaml_content=None)


def test_environment_wins_over_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _build(monkeypatch, tmp_path, env={"BOT_USERNAME": "FromEnvBot"})
    assert settings.bot_username == "FromEnvBot"


def test_bot_username_strips_leading_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_content = VALID_YAML.replace("bot_username: TigrinhoDaCopaBot", "bot_username: '@MyBot'")
    settings = _build(monkeypatch, tmp_path, yaml_content=yaml_content)
    assert settings.bot_username == "MyBot"


def test_invalid_timezone_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_content = VALID_YAML.replace("America/Sao_Paulo", "Not/ARealZone")
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, yaml_content=yaml_content)


def test_invalid_sync_time_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_content = VALID_YAML.replace('sync_time: "06:00"', 'sync_time: "25:99"')
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, yaml_content=yaml_content)


def test_invalid_provider_mode_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_content = VALID_YAML.replace("provider_mode: fake", "provider_mode: bogus")
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, yaml_content=yaml_content)


def test_derived_properties(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.sync_time_obj == time(6, 0)
    assert settings.tzinfo == ZoneInfo("America/Sao_Paulo")
    assert settings.budget_tzinfo == ZoneInfo("UTC")


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(VALID_YAML, encoding="utf-8")
    for name in _FIELD_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(cfg))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-123")
    monkeypatch.setenv("API_FOOTBALL_KEY", "key-123")
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()


def test_reminder_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.reminder_lead_minutes == 60
    assert settings.reminder_interval_minutes == 10
    assert settings.reminder_lead == timedelta(minutes=60)


def test_reminder_interval_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, env={"REMINDER_INTERVAL_MINUTES": "0"})


def test_reminder_lead_must_be_positive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, env={"REMINDER_LEAD_MINUTES": "0"})
