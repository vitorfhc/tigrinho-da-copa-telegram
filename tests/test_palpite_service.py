"""Tests for the telegram-free AI palpite service (generation + caching + loading)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from tigrinho.ai.schemas import GamePalpite
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import PalpiteRepository
from tigrinho.palpite_service import (
    PALPITE_HORIZON,
    generate_palpites,
    load_today_palpites,
)

_TODAY = date(2026, 6, 16)


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed_game(
    session_factory: sessionmaker[Session],
    fixture_id: int,
    hours: float,
    status: GameStatus = GameStatus.SCHEDULED,
) -> None:
    kickoff = _now() + timedelta(hours=hours)
    with session_factory() as session:
        session.add(
            Game(
                fixture_id=fixture_id,
                match_hash=f"h{fixture_id}",
                stage=Stage.GROUP,
                home_team_id=fixture_id * 10,
                home_team_name=f"Home{fixture_id}",
                away_team_id=fixture_id * 10 + 1,
                away_team_name=f"Away{fixture_id}",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=status,
            )
        )
        session.commit()


def _palpite_json(fixture_id: int) -> str:
    return GamePalpite(
        fixture_id=fixture_id,
        analysis="análise",
        exact_score={"home": 1, "away": 0},  # type: ignore[arg-type]
        first_team="HOME",  # type: ignore[arg-type]
        btts="ONLY_HOME",  # type: ignore[arg-type]
        winner="HOME",  # type: ignore[arg-type]
        over_under="UNDER",  # type: ignore[arg-type]
    ).model_dump_json()


class FakeGenerator:
    """A PalpiteGenerator that returns canned JSON for a fixed set of fixture ids."""

    def __init__(self, fixture_ids: list[int]) -> None:
        self._fixture_ids = fixture_ids
        self.calls = 0
        self.last_user_content = ""

    async def generate(self, *, system_instruction: str, user_content: str) -> str:
        self.calls += 1
        self.last_user_content = user_content
        items = ", ".join(_palpite_json(fid) for fid in self._fixture_ids)
        return '{"palpites": [' + items + "]}"


async def test_generates_and_saves_for_upcoming_games(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, 2)
    _seed_game(session_factory, 2, 5)
    gen = FakeGenerator([1, 2])

    generated = await generate_palpites(
        session_factory, gen, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert sorted(generated) == [1, 2]
    assert gen.calls == 1
    with session_factory() as s:
        assert PalpiteRepository(s).existing_fixture_ids([1, 2], _TODAY) == {1, 2}


async def test_cache_skips_regeneration_when_all_present(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, 2)
    gen = FakeGenerator([1])
    await generate_palpites(
        session_factory, gen, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    gen2 = FakeGenerator([1])
    generated = await generate_palpites(
        session_factory, gen2, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert generated == []
    assert gen2.calls == 0  # nothing missing -> Gemini is never called


async def test_only_missing_games_are_generated(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, 2)
    _seed_game(session_factory, 2, 5)
    with session_factory() as s:
        PalpiteRepository(s).upsert(
            fixture_id=1, palpite_date=_TODAY, payload_json=_palpite_json(1)
        )
        s.commit()
    gen = FakeGenerator([2])

    generated = await generate_palpites(
        session_factory, gen, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert generated == [2]
    assert "fixture_id=2" in gen.last_user_content
    assert "fixture_id=1" not in gen.last_user_content  # game 1 already cached


async def test_unknown_fixture_in_response_is_ignored(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, 2)
    gen = FakeGenerator([1, 999])  # 999 was never requested

    await generate_palpites(
        session_factory, gen, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    with session_factory() as s:
        assert PalpiteRepository(s).get(999, _TODAY) is None
        assert PalpiteRepository(s).get(1, _TODAY) is not None


async def test_generates_for_live_games(
    session_factory: sessionmaker[Session],
) -> None:
    # A game that already kicked off and is in progress (LIVE) is also eligible for a palpite.
    _seed_game(session_factory, 1, -1, status=GameStatus.LIVE)
    gen = FakeGenerator([1])

    generated = await generate_palpites(
        session_factory, gen, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert generated == [1]
    assert gen.calls == 1


def test_load_today_palpites_includes_live_games(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, -1, status=GameStatus.LIVE)
    with session_factory() as s:
        PalpiteRepository(s).upsert(
            fixture_id=1, palpite_date=_TODAY, payload_json=_palpite_json(1)
        )
        s.commit()

    rendered = load_today_palpites(
        session_factory, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert [r.fixture_id for r in rendered] == [1]


async def test_no_upcoming_games_does_not_call_generator(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, 40)  # beyond the 24h horizon
    gen = FakeGenerator([1])

    generated = await generate_palpites(
        session_factory, gen, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert generated == []
    assert gen.calls == 0


def test_load_today_palpites_returns_cached_and_skips_missing(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_game(session_factory, 1, 2)
    _seed_game(session_factory, 2, 5)
    with session_factory() as s:
        PalpiteRepository(s).upsert(
            fixture_id=1, palpite_date=_TODAY, payload_json=_palpite_json(1)
        )
        s.commit()

    rendered = load_today_palpites(
        session_factory, now=_now(), palpite_date=_TODAY, live_window_hours=3
    )

    assert [r.fixture_id for r in rendered] == [1]  # game 2 has no cached palpite -> skipped
    assert rendered[0].home_team == "Home1"
    assert rendered[0].palpite.winner.value == "HOME"


def test_palpite_horizon_is_24h() -> None:
    assert timedelta(hours=24) == PALPITE_HORIZON
