"""Shared pytest fixtures: a fresh temp-SQLite engine + session per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from tigrinho.bot.runtime import AppContext
from tigrinho.config import Settings
from tigrinho.db.engine import create_all, create_db_engine, create_session_factory
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.fake import FakeProvider

_SETTINGS_ENV_NAMES = [
    "CONFIG_PATH",
    "TELEGRAM_BOT_TOKEN",
    "API_FOOTBALL_KEY",
    "GROUP_CHAT_ID",
    "ADMIN_USER_ID",
    "BOT_USERNAME",
    "PROVIDER_MODE",
]


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_db_engine(str(tmp_path / "test.db"))
    create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as db_session:
        yield db_session


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    # chdir + clear field env so neither a real .env/config.yaml nor host env leaks in.
    monkeypatch.chdir(tmp_path)
    for name in _SETTINGS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return Settings(
        telegram_bot_token="123456:dummy-token",
        api_football_key="test-key",
        group_chat_id=-1001234567890,
        admin_user_id=999,
        bot_username="TigrinhoDaCopaBot",
        provider_mode="fake",
    )


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def app_context(
    settings: Settings,
    session_factory: sessionmaker[Session],
    fake_provider: FakeProvider,
) -> AppContext:
    budget = RequestBudget(
        session_factory,
        daily_cap=settings.api_daily_cap,
        reset_tz=settings.budget_tzinfo,
    )
    return AppContext(
        settings=settings,
        provider=fake_provider,
        session_factory=session_factory,
        budget=budget,
    )
