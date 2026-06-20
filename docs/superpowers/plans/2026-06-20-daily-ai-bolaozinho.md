# Daily AI Bolãozinho Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each evening, a dedicated Gemini flow grades tomorrow's World Cup fixtures on six binary interest criteria and auto-opens a bolãozinho over the best ≤2 games.

**Architecture:** A new `run_daily` job (`daily_bolao_job`) calls an async orchestrator (`create_daily_bolao`) that queries tomorrow's `SCHEDULED` games, asks a new `GameScorer` (Gemini, independent of `/palpite`) for six yes/no grades per game, ranks by count-of-trues in pure domain code, then reuses the existing `create_tournament`/`add_game`/`open_tournament` service + a shared `announce_open` helper to open the pot exactly like a manual `/bolaozinho_abrir`. No heuristic fallback — failures DM the admin and create nothing. Idempotency is guaranteed by a UNIQUE `auto_created_for` column + `IntegrityError` handling.

**Tech Stack:** Python 3.12, python-telegram-bot 21.x (`JobQueue.run_daily`), SQLAlchemy 2.0 (sync) + Alembic, google-genai (`gemini-3.1-pro-preview`, Google-Search grounding), pydantic / pydantic-settings, structlog, pytest + pytest-asyncio.

## Global Constraints

- Python **3.12+**; **mypy --strict** must pass with **no `Any` in `domain/`**.
- All four gates pass before every commit: `ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest`.
- **Domain logic stays pure** (no I/O, clock, or DB in `tigrinho/domain/`); `tigrinho/domain/daily_bolao.py` joins the **100% line+branch** coverage gate (`--cov-fail-under=100`).
- **Append-only Alembic migrations** — never edit/delete existing ones. New migration `down_revision = "d4e5f6a7b8c9"` (current head).
- **Secrets split:** the only secret is `GEMINI_API_KEY` (already in `.env`); all new settings live in `config.yaml`.
- **HTML parse mode everywhere**; dynamic strings rendered through `escape()`.
- **Binary scoring only:** the model emits only booleans; the 0–6 interest is computed in pure code.
- **No fallback:** Gemini error / unparseable `scores` / zero usable picks ⇒ create nothing, DM admin.
- **Maintenance rule:** the behaviour change updates `/ajuda` text **and** `COMPLETION.md` (§24) in this work.
- Spec: `docs/superpowers/specs/2026-06-20-daily-ai-bolaozinho-design.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `tigrinho/config.py` (modify) | three new settings + `daily_bolao_time_obj` + `@model_validator` |
| `tigrinho/domain/daily_bolao.py` (create) | PURE: `Candidate`, `InterestCriteria`, `interest()`, `rank_and_select()`, `local_day_window_utc()` |
| `tigrinho/ai/daily_bolao.py` (create) | pydantic scoring models, `parse_scoring()`, `sanitize_name()`, `build_scoring_prompt()` |
| `tigrinho/ai/base.py` (modify) | `GameScorer` Protocol |
| `tigrinho/ai/gemini.py` (modify) | `GeminiGameScorer` client |
| `tigrinho/db/models.py` (modify) | `Tournament.auto_created_for` + `UniqueConstraint` |
| `tigrinho/db/repositories.py` (modify) | `GameRepository.list_scheduled_in_window()`, `TournamentRepository.daily_auto_for()` |
| `tigrinho/db/migrations/versions/f7a8b9c0d1e2_add_tournament_auto_created_for.py` (create) | the migration |
| `tigrinho/daily_bolao_service.py` (create) | async orchestrator `create_daily_bolao()` |
| `tigrinho/bot/tournament_handlers.py` (modify) | shared `announce_open()`; `cmd_abrir` + `_do_open` call it |
| `tigrinho/bot/daily_bolao_job.py` (create) | `daily_bolao_job()` + `schedule_daily_bolao_job()` |
| `tigrinho/bot/runtime.py` (modify) | `AppContext.game_scorer` |
| `tigrinho/__main__.py` (modify) | `make_game_scorer()` + wiring |
| `tigrinho/bot/app.py` (modify) | schedule the job when enabled |
| `pyproject.toml` (modify) | add `--cov=tigrinho.domain.daily_bolao` |
| `config.example.yaml`, `COMPLETION.md`, `PROGRESS.md`, `/ajuda` text (modify) | docs |

**Note on `announce_open` placement:** it lives in `tournament_handlers.py` next to `_post_open_announcement` / `_broadcast_open_dm` (which it wraps), not in `tournament_announce.py`. Moving the helpers to `tournament_announce.py` would force `tournament_announce → tournament_handlers` while `cmd_abrir`/`_do_open` call back into it — a circular import. Co-locating avoids that; the job imports `announce_open` from `tournament_handlers` (one direction). This refines spec §7's suggested file.

---

## Task 1: Config — three settings + validator

**Files:**
- Modify: `tigrinho/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.daily_bolao_enabled: bool`, `Settings.daily_bolao_time: str`, `Settings.daily_bolao_entry_price_cents: int`, `Settings.daily_bolao_time_obj -> time` property; a `@model_validator(mode="after")` that raises when `daily_bolao_enabled` without `gemini_api_key` or with `daily_bolao_entry_price_cents <= 0`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`. First extend `_FIELD_ENV_NAMES` (so host env can't leak) by adding these three entries to the list:

```python
    "DAILY_BOLAO_ENABLED",
    "DAILY_BOLAO_TIME",
    "DAILY_BOLAO_ENTRY_PRICE_CENTS",
```

Then append these tests:

```python
def test_daily_bolao_defaults_and_time_property(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _build(monkeypatch, tmp_path)
    assert settings.daily_bolao_enabled is False
    assert settings.daily_bolao_time == "18:00"
    assert settings.daily_bolao_entry_price_cents == 1000
    assert settings.daily_bolao_time_obj == time(18, 0)


def test_daily_bolao_enabled_without_key_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        _build(monkeypatch, tmp_path, env={"DAILY_BOLAO_ENABLED": "true"})


def test_daily_bolao_enabled_with_zero_price_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        _build(
            monkeypatch,
            tmp_path,
            env={
                "DAILY_BOLAO_ENABLED": "true",
                "GEMINI_API_KEY": "k-123",
                "DAILY_BOLAO_ENTRY_PRICE_CENTS": "0",
            },
        )


def test_daily_bolao_enabled_with_key_and_price_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _build(
        monkeypatch,
        tmp_path,
        env={"DAILY_BOLAO_ENABLED": "true", "GEMINI_API_KEY": "k-123"},
    )
    assert settings.daily_bolao_enabled is True
    assert settings.daily_bolao_entry_price_cents == 1000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -k daily_bolao -v`
Expected: FAIL (`daily_bolao_enabled` is not a field / AttributeError).

- [ ] **Step 3: Implement the config changes**

In `tigrinho/config.py`, change the pydantic import to add `model_validator`:

```python
from pydantic import Field, field_validator, model_validator
```

Add the three fields just after the `gemini_model` field (in the config.yaml block):

```python
    # Daily AI-curated bolãozinho (§24): an evening job grades tomorrow's fixtures on binary
    # interest criteria and auto-opens a bolãozinho over the best ≤2 games. Opt-in (off by
    # default); when enabled it requires GEMINI_API_KEY (validated below) and a positive price.
    daily_bolao_enabled: bool = False
    daily_bolao_time: str = "18:00"
    daily_bolao_entry_price_cents: int = 1000
```

Add `daily_bolao_time` to the existing clock-time validator decorator:

```python
    @field_validator("sync_time", "palpite_time", "daily_bolao_time")
```

Add the cross-field validator (place it after the `_valid_clock_time` validator):

```python
    @model_validator(mode="after")
    def _validate_daily_bolao(self) -> Settings:
        if self.daily_bolao_enabled:
            if not self.gemini_api_key:
                raise ValueError("daily_bolao_enabled requires GEMINI_API_KEY to be set")
            if self.daily_bolao_entry_price_cents <= 0:
                raise ValueError(
                    "daily_bolao_entry_price_cents must be > 0 when daily_bolao_enabled"
                )
        return self
```

Add the property next to `palpite_time_obj`:

```python
    @property
    def daily_bolao_time_obj(self) -> time:
        """The configured daily-bolãozinho creation time (local to ``timezone``; §24)."""
        hours, minutes = self.daily_bolao_time.split(":")
        return time(int(hours), int(minutes))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -k daily_bolao -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Update `config.example.yaml`**

Add under the existing settings:

```yaml
# Daily AI-curated bolãozinho (§24). When daily_bolao_enabled is true, an evening job at
# daily_bolao_time grades the next day's fixtures with Gemini and auto-opens a bolãozinho over
# the best up-to-2 games. Requires GEMINI_API_KEY. Price is in integer cents (1000 = R$ 10,00).
daily_bolao_enabled: false
daily_bolao_time: "18:00"
daily_bolao_entry_price_cents: 1000
```

- [ ] **Step 6: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest tests/test_config.py -q
git add tigrinho/config.py tests/test_config.py config.example.yaml
git commit -m "feat(config): daily_bolao settings + enabled-requires-key validator (§24)"
```

---

## Task 2: Pure domain — scoring, selection, window

**Files:**
- Create: `tigrinho/domain/daily_bolao.py`
- Modify: `pyproject.toml` (coverage gate)
- Test: `tests/test_daily_bolao_pure.py`

**Interfaces:**
- Produces:
  - `Candidate(fixture_id: int, kickoff_utc: datetime)` (frozen)
  - `InterestCriteria(decisive, quality_matchup, rivalry_or_storyline, star_power, competitive_balance, goal_potential: bool)` (frozen)
  - `interest(c: InterestCriteria) -> int`
  - `rank_and_select(candidates: Sequence[Candidate], scores: Mapping[int, int], *, limit: int = 2) -> list[int]`
  - `local_day_window_utc(target_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily_bolao_pure.py`:

```python
"""Pure tests for the daily-bolãozinho scoring/selection/window helpers (§24)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tigrinho.domain.daily_bolao import (
    Candidate,
    InterestCriteria,
    interest,
    local_day_window_utc,
    rank_and_select,
)


def _crit(n_true: int) -> InterestCriteria:
    flags = [i < n_true for i in range(6)]
    return InterestCriteria(*flags)


def test_interest_counts_true_grades() -> None:
    assert interest(InterestCriteria(False, False, False, False, False, False)) == 0
    assert interest(InterestCriteria(True, True, True, True, True, True)) == 6
    assert interest(InterestCriteria(True, False, True, False, True, False)) == 3


def _cand(fid: int, hour: int) -> Candidate:
    return Candidate(fixture_id=fid, kickoff_utc=datetime(2026, 6, 21, hour, 0))


def test_rank_and_select_picks_top_two_by_interest() -> None:
    candidates = [_cand(1, 13), _cand(2, 16), _cand(3, 19)]
    scores = {1: 2, 2: 5, 3: 4}
    assert rank_and_select(candidates, scores) == [2, 3]


def test_rank_and_select_tie_breaks_on_earlier_kickoff() -> None:
    candidates = [_cand(1, 19), _cand(2, 13), _cand(3, 16)]
    scores = {1: 4, 2: 4, 3: 4}
    assert rank_and_select(candidates, scores) == [2, 3]


def test_rank_and_select_drops_hallucinated_ids() -> None:
    candidates = [_cand(1, 13), _cand(2, 16)]
    scores = {1: 3, 2: 2, 999: 6}  # 999 is not a real candidate
    assert rank_and_select(candidates, scores) == [1, 2]


def test_rank_and_select_partial_coverage_returns_subset() -> None:
    candidates = [_cand(1, 13), _cand(2, 16), _cand(3, 19)]
    scores = {2: 4}  # Gemini scored only one of three
    assert rank_and_select(candidates, scores) == [2]


def test_rank_and_select_empty_intersection_returns_empty() -> None:
    candidates = [_cand(1, 13)]
    assert rank_and_select(candidates, {999: 6}) == []


def test_rank_and_select_respects_limit() -> None:
    candidates = [_cand(1, 13), _cand(2, 16), _cand(3, 19)]
    scores = {1: 1, 2: 2, 3: 3}
    assert rank_and_select(candidates, scores, limit=1) == [3]


def test_local_day_window_utc_is_next_local_day_in_utc() -> None:
    tz = ZoneInfo("America/Sao_Paulo")  # UTC-3, no DST
    start, end = local_day_window_utc(date(2026, 6, 21), tz)
    assert start == datetime(2026, 6, 21, 3, 0)  # 00:00 local == 03:00 UTC
    assert end == datetime(2026, 6, 22, 3, 0)
    assert start.tzinfo is None and end.tzinfo is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_bolao_pure.py -v`
Expected: FAIL (`No module named 'tigrinho.domain.daily_bolao'`).

- [ ] **Step 3: Implement the pure module**

Create `tigrinho/domain/daily_bolao.py`:

```python
"""Pure scoring, selection, and date-window helpers for the daily AI bolãozinho (§24).

PURE: no I/O, clock, or DB. The interest "score" is the count of the six binary criteria that
are true — the only numeric value, and it is derived purely from booleans (never emitted by the
model). Selection ranks the scored candidates by ``(interest desc, kickoff asc)`` and takes the
top ``limit``, dropping any fixture id the model invented (not a real candidate).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class Candidate:
    """A fixture eligible for tomorrow's pool: its id and (naive UTC) kickoff."""

    fixture_id: int
    kickoff_utc: datetime


@dataclass(frozen=True, slots=True)
class InterestCriteria:
    """The six yes/no grades the model assigns to one game."""

    decisive: bool
    quality_matchup: bool
    rivalry_or_storyline: bool
    star_power: bool
    competitive_balance: bool
    goal_potential: bool


def interest(c: InterestCriteria) -> int:
    """The interest score: how many of the six binary criteria are true (0–6)."""
    return sum(
        (
            c.decisive,
            c.quality_matchup,
            c.rivalry_or_storyline,
            c.star_power,
            c.competitive_balance,
            c.goal_potential,
        )
    )


def rank_and_select(
    candidates: Sequence[Candidate], scores: Mapping[int, int], *, limit: int = 2
) -> list[int]:
    """Pick up to ``limit`` fixture ids: highest interest first, earliest kickoff breaks ties.

    Only fixture ids present in BOTH ``scores`` and ``candidates`` are considered (a model that
    invents an id, or omits a candidate, simply doesn't place that id). Returns ``[]`` when the
    intersection is empty — the caller treats that as a failure (no fallback).
    """
    by_id = {c.fixture_id: c for c in candidates}
    ranked = sorted(
        (
            (scores[fid], by_id[fid].kickoff_utc, fid)
            for fid in scores
            if fid in by_id
        ),
        key=lambda t: (-t[0], t[1]),
    )
    return [fid for _, _, fid in ranked[:limit]]


def local_day_window_utc(target_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The ``[start, end)`` of ``target_date`` (local) as naive-UTC datetimes (DST-safe).

    The day boundary is built in LOCAL time and the +1 day is added in local time *before*
    converting to UTC, so a DST transition (a 23h/25h local day) is handled correctly.
    """
    start_local = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(UTC).replace(tzinfo=None)
    end_utc = end_local.astimezone(UTC).replace(tzinfo=None)
    return start_utc, end_utc
```

- [ ] **Step 4: Add the module to the coverage gate**

In `pyproject.toml`, append `--cov=tigrinho.domain.daily_bolao` to the `addopts` line (after `--cov=tigrinho.domain.splitwise_ledger`):

```toml
addopts = "-q --cov=tigrinho.domain.scoring --cov=tigrinho.domain.settlement --cov=tigrinho.domain.tournament --cov=tigrinho.domain.splitwise_ledger --cov=tigrinho.domain.daily_bolao --cov-branch --cov-report=term-missing --cov-fail-under=100"
```

- [ ] **Step 5: Run tests + full coverage to verify pass**

Run: `pytest tests/test_daily_bolao_pure.py -v && pytest -q`
Expected: PASS, and `--cov-fail-under=100` still satisfied (the new module is fully covered by Step 1's tests).

- [ ] **Step 6: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/domain/daily_bolao.py tests/test_daily_bolao_pure.py pyproject.toml
git commit -m "feat(domain): pure daily_bolao scoring/selection/window (100% covered, §24)"
```

---

## Task 3: AI wire format — scoring models, parse, prompt, sanitize

**Files:**
- Create: `tigrinho/ai/daily_bolao.py`
- Test: `tests/test_daily_bolao_ai.py`

**Interfaces:**
- Consumes: `tigrinho.ai.schemas._AiModel`, `extract_json`, `strip_citation_tags`; `tigrinho.ai.prompt.GameInfo`; `tigrinho.domain.text_pt.format_kickoff_local`; `tigrinho.enums.Stage`.
- Produces:
  - `GameInterestCriteria(_AiModel)` (six bools)
  - `GameInterestScore(_AiModel)`: `fixture_id: int`, `criteria: GameInterestCriteria`
  - `DailyBolaoScoring(_AiModel)`: `name: str = ""`, `scores: list[GameInterestScore]`
  - `sanitize_name(raw: str) -> str`
  - `parse_scoring(text: str) -> DailyBolaoScoring`
  - `build_scoring_prompt(games: Sequence[GameInfo]) -> tuple[str, str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily_bolao_ai.py`:

```python
"""Tests for the daily-bolãozinho AI wire format: models, parse, sanitize, prompt (§24)."""

from __future__ import annotations

from datetime import datetime

import pytest

from tigrinho.ai.daily_bolao import (
    DailyBolaoScoring,
    GameInterestCriteria,
    GameInterestScore,
    build_scoring_prompt,
    parse_scoring,
    sanitize_name,
)
from tigrinho.ai.prompt import GameInfo
from tigrinho.enums import Stage


def _score_json(fid: int) -> str:
    return GameInterestScore(
        fixture_id=fid,
        criteria=GameInterestCriteria(
            decisive=True,
            quality_matchup=True,
            rivalry_or_storyline=False,
            star_power=True,
            competitive_balance=False,
            goal_potential=True,
        ),
    ).model_dump_json()


def test_parse_scoring_happy_path() -> None:
    raw = '{"name": "Clássico do Dia", "scores": [' + _score_json(1) + "]}"
    batch = parse_scoring(raw)
    assert batch.name == "Clássico do Dia"
    assert batch.scores[0].fixture_id == 1
    assert batch.scores[0].criteria.decisive is True


def test_parse_scoring_handles_code_fence_and_prose() -> None:
    raw = "Aqui está:\n```json\n" + '{"name": "X", "scores": [' + _score_json(7) + "]}\n```"
    batch = parse_scoring(raw)
    assert batch.scores[0].fixture_id == 7


def test_parse_scoring_name_omitted_defaults_to_empty() -> None:
    raw = '{"scores": [' + _score_json(1) + "]}"
    batch = parse_scoring(raw)
    assert batch.name == ""
    assert batch.scores[0].fixture_id == 1


def test_parse_scoring_bad_scores_raises() -> None:
    with pytest.raises(ValueError):
        parse_scoring('{"name": "X"}')  # missing required `scores`


def test_sanitize_name_strips_citation_tags_and_collapses() -> None:
    assert sanitize_name("Clássico [1] do   Dia\n") == "Clássico do Dia"


def test_sanitize_name_truncates_to_60_chars() -> None:
    assert len(sanitize_name("A" * 200)) == 60


def test_sanitize_name_empty_when_only_citations() -> None:
    assert sanitize_name("  [1] [2.3]  ") == ""


def test_build_scoring_prompt_lists_each_game_and_forbids_numbers() -> None:
    games = [
        GameInfo(1, "Brasil", "Argentina", datetime(2026, 6, 21, 16, 0), Stage.KNOCKOUT),
        GameInfo(2, "França", "Alemanha", datetime(2026, 6, 21, 13, 0), Stage.GROUP),
    ]
    system, user = build_scoring_prompt(games)
    assert "fixture_id=1" in user and "fixture_id=2" in user
    assert "mata-mata" in user and "fase de grupos" in user
    assert "decisive" in system  # criteria are named in the instruction
    # the model must not emit a number/ranking
    assert "JSON" in system
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_bolao_ai.py -v`
Expected: FAIL (`No module named 'tigrinho.ai.daily_bolao'`).

- [ ] **Step 3: Implement the AI wire module**

Create `tigrinho/ai/daily_bolao.py`:

```python
"""Wire format + prompt for the daily-bolãozinho game-interest scorer (§24).

Binary-only: the model grades six yes/no criteria per game and proposes a pool name. The numeric
interest score is computed elsewhere (``tigrinho.domain.daily_bolao.interest``) — the prompt
forbids the model from emitting any number or ranking. JSON is requested in the prompt and
validated here (mirrors ``ai/schemas.py``); ``name`` is optional so a missing name degrades to a
deterministic fallback rather than failing the whole pool.
"""

from __future__ import annotations

from collections.abc import Sequence

from tigrinho.ai.prompt import GameInfo
from tigrinho.ai.schemas import _AiModel, extract_json, strip_citation_tags
from tigrinho.domain.text_pt import format_kickoff_local
from tigrinho.enums import Stage

_MAX_NAME_LEN = 60


class GameInterestCriteria(_AiModel):
    """Six yes/no grades for one game (all booleans; the only thing the model scores)."""

    decisive: bool
    quality_matchup: bool
    rivalry_or_storyline: bool
    star_power: bool
    competitive_balance: bool
    goal_potential: bool


class GameInterestScore(_AiModel):
    """One game's binary grades, echoing the requested ``fixture_id``."""

    fixture_id: int
    criteria: GameInterestCriteria


class DailyBolaoScoring(_AiModel):
    """The model's full response: a proposed pool name + per-game binary grades."""

    name: str = ""
    scores: list[GameInterestScore]


def sanitize_name(raw: str) -> str:
    """Clean an untrusted model-proposed name: drop citation tags, collapse whitespace, cap length.

    HTML safety is NOT done here — rendering escapes dynamic strings like everywhere else. Returns
    ``""`` when nothing usable remains, so the caller substitutes the dated fallback.
    """
    cleaned = strip_citation_tags(raw)
    cleaned = " ".join(cleaned.split())
    return cleaned[:_MAX_NAME_LEN].strip()


def parse_scoring(text: str) -> DailyBolaoScoring:
    """Extract + validate a model response into a :class:`DailyBolaoScoring` (raises on bad data)."""
    return DailyBolaoScoring.model_validate_json(extract_json(text))


_SYSTEM_INSTRUCTION = """\
Você é um analista profissional de futebol especializado na Copa do Mundo FIFA 2026. \
Para CADA jogo informado, avalie SEIS critérios binários (sim/não) sobre o quão interessante \
seria apostar nele. Use a Pesquisa Google (web) para informações recentes (forma, importância, \
escalações). NÃO dê nenhuma nota numérica nem ranqueamento — apenas os booleanos.

Critérios (cada um true ou false):
- decisive: é mata-mata, ou o resultado decide classificação/posição no grupo.
- quality_matchup: as duas seleções são fortes / de destaque.
- rivalry_or_storyline: há rivalidade histórica ou um enredo marcante.
- star_power: é provável que um craque mundialmente famoso entre em campo.
- competitive_balance: jogo equilibrado, difícil de prever.
- goal_potential: tende a ser aberto / com muitos gols.

Proponha também UM nome curto e divertido (pt-BR) para um bolão do dia sobre os melhores jogos.

Responda com APENAS UM objeto JSON (sem texto fora do JSON, sem markdown), neste formato exato:
{
  "name": "<nome curto e divertido em pt-BR>",
  "scores": [
    {
      "fixture_id": <int, exatamente o id informado>,
      "criteria": {
        "decisive": <bool>,
        "quality_matchup": <bool>,
        "rivalry_or_storyline": <bool>,
        "star_power": <bool>,
        "competitive_balance": <bool>,
        "goal_potential": <bool>
      }
    }
  ]
}
Inclua exatamente um item por jogo informado, ecoando o fixture_id correspondente."""


def _stage_label(stage: Stage) -> str:
    return "mata-mata" if stage is Stage.KNOCKOUT else "fase de grupos"


def build_scoring_prompt(games: Sequence[GameInfo]) -> tuple[str, str]:
    """Return ``(system_instruction, user_content)`` for grading the day's games."""
    lines = [
        f"- fixture_id={g.fixture_id} | {g.home_team} x {g.away_team} | "
        f"{format_kickoff_local(g.kickoff_local)} | {_stage_label(g.stage)}"
        for g in games
    ]
    user_content = (
        "Avalie os seguintes jogos da Copa do Mundo 2026 (um item por jogo):\n\n"
        + "\n".join(lines)
    )
    return _SYSTEM_INSTRUCTION, user_content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_daily_bolao_ai.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/ai/daily_bolao.py tests/test_daily_bolao_ai.py
git commit -m "feat(ai): daily_bolao binary-scoring models + prompt + sanitize (§24)"
```

---

## Task 4: Gemini flow — `GameScorer` protocol + `GeminiGameScorer`

**Files:**
- Modify: `tigrinho/ai/base.py`, `tigrinho/ai/gemini.py`
- Test: `tests/test_gemini_generator.py`

**Interfaces:**
- Produces:
  - `GameScorer` Protocol: `async def score_games(self, *, system_instruction: str, user_content: str) -> str`
  - `GeminiGameScorer(*, api_key: str, model: str)` implementing it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gemini_generator.py` (the `_install_fake_client` helper already patches `tigrinho.ai.gemini.genai.Client`, so it works for the new class too). Add the import and test:

```python
from tigrinho.ai.gemini import GeminiGameScorer  # add alongside the existing import


async def test_score_games_passes_grounding_and_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _install_fake_client(monkeypatch, captured, text='{"name": "X", "scores": []}')
    scorer = GeminiGameScorer(api_key="secret", model="gemini-3.1-pro-preview")

    out = await scorer.score_games(system_instruction="SYS", user_content="USER")

    assert out == '{"name": "X", "scores": []}'
    assert captured["model"] == "gemini-3.1-pro-preview"
    assert captured["contents"] == "USER"
    config = captured["config"]
    assert config.system_instruction == "SYS"
    assert config.thinking_config.thinking_level.value == "HIGH"
    assert config.tools[0].google_search is not None


async def test_score_games_empty_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_client(monkeypatch, captured, text=None)
    scorer = GeminiGameScorer(api_key="secret", model="gemini-3.1-pro-preview")
    with pytest.raises(ValueError):
        await scorer.score_games(system_instruction="SYS", user_content="USER")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gemini_generator.py -k score_games -v`
Expected: FAIL (`cannot import name 'GeminiGameScorer'`).

- [ ] **Step 3: Implement the protocol + client**

Append to `tigrinho/ai/base.py`:

```python
@runtime_checkable
class GameScorer(Protocol):
    """Grades candidate games on binary interest criteria; returns raw model text (JSON; §24)."""

    async def score_games(self, *, system_instruction: str, user_content: str) -> str: ...
```

Append to `tigrinho/ai/gemini.py` (reuses the module's existing `genai`, `types`, `_log`):

```python
class GeminiGameScorer:
    """Daily-bolãozinho game-interest scorer — a separate Gemini flow from /palpite (§24).

    Same grounded-call conventions as :class:`GeminiPalpiteGenerator` (Google Search + high
    thinking), but a distinct method/schema so the two flows never share state.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def score_games(self, *, system_instruction: str, user_content: str) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=config,
        )
        text = response.text
        if not text:
            raise ValueError("Gemini returned an empty game-scoring response")
        _log.info("game_scoring_generated", model=self._model, chars=len(text))
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gemini_generator.py -k score_games -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/ai/base.py tigrinho/ai/gemini.py tests/test_gemini_generator.py
git commit -m "feat(ai): GameScorer protocol + GeminiGameScorer client (§24)"
```

---

## Task 5: DB — `auto_created_for` column, UNIQUE constraint, migration, repo methods

**Files:**
- Modify: `tigrinho/db/models.py`, `tigrinho/db/repositories.py`
- Create: `tigrinho/db/migrations/versions/f7a8b9c0d1e2_add_tournament_auto_created_for.py`
- Test: `tests/test_migrations.py`, `tests/test_daily_bolao_repo.py`

**Interfaces:**
- Produces:
  - `Tournament.auto_created_for: Mapped[date | None]` + `UniqueConstraint("auto_created_for", name="uq_tournament_auto_created_for")`
  - `GameRepository.list_scheduled_in_window(start_utc: datetime, end_utc: datetime) -> list[Game]`
  - `TournamentRepository.daily_auto_for(target_date: date) -> Tournament | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_migrations.py`:

```python
def test_tournaments_auto_created_for_unique_after_upgrade(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'mig.db'}"
    command.upgrade(_alembic_config(db_url), "head")
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("tournaments")}
        assert "auto_created_for" in cols
        uniques = inspector.get_unique_constraints("tournaments")
        names = {uc["name"] for uc in uniques}
        assert "uq_tournament_auto_created_for" in names
    finally:
        engine.dispose()
```

Create `tests/test_daily_bolao_repo.py`:

```python
"""Repo tests for the daily-bolãozinho queries (§24)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tigrinho.db.models import Base, Game, GameStatus, Stage, TournamentStatus
from tigrinho.db.repositories import GameRepository, TournamentRepository


def _session_factory() -> sessionmaker:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _game(fid: int, kickoff: datetime, status: GameStatus = GameStatus.SCHEDULED) -> Game:
    return Game(
        fixture_id=fid,
        match_hash=f"h{fid}",
        stage=Stage.GROUP,
        home_team_id=fid * 10,
        home_team_name=f"Home{fid}",
        away_team_id=fid * 10 + 1,
        away_team_name=f"Away{fid}",
        kickoff_utc=kickoff,
        kickoff_local=kickoff,
        status=status,
    )


def test_list_scheduled_in_window_filters_by_status_and_bounds() -> None:
    sf = _session_factory()
    with sf() as s:
        s.add(_game(1, datetime(2026, 6, 21, 13, 0)))  # in window
        s.add(_game(2, datetime(2026, 6, 21, 23, 0)))  # in window
        s.add(_game(3, datetime(2026, 6, 22, 3, 0)))  # == end, excluded (half-open)
        s.add(_game(4, datetime(2026, 6, 20, 13, 0)))  # before window
        s.add(_game(5, datetime(2026, 6, 21, 16, 0), status=GameStatus.FINISHED))  # not scheduled
        s.commit()
    with sf() as s:
        games = GameRepository(s).list_scheduled_in_window(
            datetime(2026, 6, 21, 3, 0), datetime(2026, 6, 22, 3, 0)
        )
    assert [g.fixture_id for g in games] == [1, 2]


def test_daily_auto_for_finds_only_matching_date() -> None:
    sf = _session_factory()
    with sf() as s:
        repo = TournamentRepository(s)
        t = repo.create(name="Dia 21", entry_price_cents=1000, created_by=42)
        t.auto_created_for = date(2026, 6, 21)
        s.commit()
    with sf() as s:
        repo = TournamentRepository(s)
        assert repo.daily_auto_for(date(2026, 6, 21)) is not None
        assert repo.daily_auto_for(date(2026, 6, 22)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_bolao_repo.py tests/test_migrations.py -k "auto_created_for or scheduled_in_window or daily_auto" -v`
Expected: FAIL (no `auto_created_for` attribute / no `list_scheduled_in_window`).

- [ ] **Step 3: Implement the model change**

In `tigrinho/db/models.py`, inside `class Tournament`, add the column after `splitwise_admin_notified_at`:

```python
    # The local calendar date a daily AI-curated bolãozinho covers (§24). NULL for every manually
    # created bolãozinho; UNIQUE so concurrent daily-job fires can't create two pots for one day.
    auto_created_for: Mapped[date | None] = mapped_column(Date, default=None)
```

And add `__table_args__` after the `entries` relationship:

```python
    __table_args__ = (
        UniqueConstraint("auto_created_for", name="uq_tournament_auto_created_for"),
    )
```

(`Date`, `UniqueConstraint`, and `date` are already imported in this file.)

- [ ] **Step 4: Implement the repo methods**

In `tigrinho/db/repositories.py`, ensure `date` is importable — change the datetime import to include it:

```python
from datetime import date, datetime, timedelta
```

Add to `GameRepository` (after `list_upcoming_within`):

```python
    def list_scheduled_in_window(
        self, start_utc: datetime, end_utc: datetime
    ) -> list[Game]:
        """SCHEDULED games kicking off in ``[start_utc, end_utc)`` — tomorrow's slate (§24)."""
        stmt = (
            select(Game)
            .where(
                Game.status == GameStatus.SCHEDULED,
                Game.kickoff_utc >= start_utc,
                Game.kickoff_utc < end_utc,
            )
            .order_by(Game.kickoff_utc)
        )
        return list(self._session.execute(stmt).scalars())
```

Add to `TournamentRepository` (after `get`):

```python
    def daily_auto_for(self, target_date: date) -> Tournament | None:
        """The daily AI bolãozinho already created for ``target_date``, if any (§24)."""
        stmt = select(Tournament).where(Tournament.auto_created_for == target_date)
        return self._session.execute(stmt).scalars().first()
```

- [ ] **Step 5: Create the migration**

Create `tigrinho/db/migrations/versions/f7a8b9c0d1e2_add_tournament_auto_created_for.py`:

```python
"""add tournaments.auto_created_for + unique constraint (daily AI bolãozinho)

Marks a bolãozinho as the daily AI-curated pool for a local calendar date (§24). NULL for every
manually created bolãozinho; UNIQUE so two concurrent daily-job fires (or a redeploy overlapping
the run time) cannot create two pots for the same day. Existing rows default to NULL.

Revision ID: f7a8b9c0d1e2
Revises: d4e5f6a7b8c9
Create Date: 2026-06-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tournaments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("auto_created_for", sa.Date(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_tournament_auto_created_for", ["auto_created_for"]
        )


def downgrade() -> None:
    with op.batch_alter_table("tournaments", schema=None) as batch_op:
        batch_op.drop_constraint("uq_tournament_auto_created_for", type_="unique")
        batch_op.drop_column("auto_created_for")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_daily_bolao_repo.py tests/test_migrations.py -v`
Expected: PASS (including `test_upgrade_head_matches_orm_metadata`, which now sees the new column on both sides).

- [ ] **Step 7: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/db/models.py tigrinho/db/repositories.py tigrinho/db/migrations/versions/f7a8b9c0d1e2_add_tournament_auto_created_for.py tests/test_migrations.py tests/test_daily_bolao_repo.py
git commit -m "feat(db): tournaments.auto_created_for + UNIQUE + repo queries (§24)"
```

---

## Task 6: Shared `announce_open` extraction

**Files:**
- Modify: `tigrinho/bot/tournament_handlers.py`
- Test: `tests/test_tournament_handlers.py`

**Interfaces:**
- Produces: `announce_open(context, settings, tournament, games, mentions) -> None` in `tournament_handlers.py`, wrapping `_post_open_announcement` + `_broadcast_open_dm`. Both `cmd_abrir` and `_do_open` call it instead of the two helpers directly. No behaviour change.

- [ ] **Step 1: Write/confirm the regression tests**

In `tests/test_tournament_handlers.py` there is already `test_open_callback_dms_known_players` covering `_do_open`. Add an explicit assertion that both open paths still post to the group AND DM players after the refactor. Add:

```python
async def test_cmd_abrir_posts_group_and_dms(app_context: AppContext) -> None:
    # A DRAFT bolãozinho with one future game and one known player.
    tournament_id, _fixture_id = _seed_open_ready_tournament(app_context)
    ctx, bot = _ctx(app_context)
    ctx.args = [str(tournament_id)]
    update = _command_update(user_id=app_context.settings.admin_user_id)

    await cmd_abrir(update, ctx)

    # group announcement + at least one player DM both went out
    chat_ids = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
    assert app_context.settings.group_chat_id in chat_ids
    assert any(cid != app_context.settings.group_chat_id for cid in chat_ids)
```

> If `_seed_open_ready_tournament`, `_ctx`, or `_command_update` helpers don't already exist in this test module, reuse the module's existing tournament-open test setup (the file already opens tournaments in `test_open_callback_dms_known_players`); mirror that helper rather than inventing a new pattern.

- [ ] **Step 2: Run the existing open tests to confirm green baseline**

Run: `pytest tests/test_tournament_handlers.py -k "open or abrir" -v`
Expected: PASS (baseline before refactor; the new `test_cmd_abrir_posts_group_and_dms` may need the seed helper — get it passing against the current code first).

- [ ] **Step 3: Add `announce_open` and rewire both call sites**

In `tigrinho/bot/tournament_handlers.py`, add the helper next to `_broadcast_open_dm`:

```python
async def announce_open(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    tournament: Tournament,
    games: list[Game],
    mentions: list[tuple[int, str]],
) -> None:
    """Post the open announcement to the group and DM every known player (§22).

    Shared by ``/bolaozinho_abrir`` (``cmd_abrir``), the inline "Abrir" callback (``_do_open``),
    and the daily AI job (``daily_bolao_job``) so an auto-opened bolãozinho is byte-identical to a
    manually-opened one.
    """
    await _post_open_announcement(context, settings, tournament, games, mentions)
    await _broadcast_open_dm(context, settings, tournament, games, mentions)
```

In `cmd_abrir`, replace the two trailing calls:

```python
    await _post_open_announcement(context, app_context.settings, tournament, games, mentions)
    await _broadcast_open_dm(context, app_context.settings, tournament, games, mentions)
```

with:

```python
    await announce_open(context, app_context.settings, tournament, games, mentions)
```

In `_do_open`, replace the same two calls (between `session.commit()` and `await query.answer(...)`) with:

```python
    await announce_open(context, app_context.settings, tournament, games, mentions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tournament_handlers.py -k "open or abrir" -v`
Expected: PASS (both `cmd_abrir` and `_do_open` paths still post + DM).

- [ ] **Step 5: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/bot/tournament_handlers.py tests/test_tournament_handlers.py
git commit -m "refactor(bolaozinho): shared announce_open for both open paths (§24 prep)"
```

---

## Task 7: Service — `create_daily_bolao`

**Files:**
- Create: `tigrinho/daily_bolao_service.py`
- Test: `tests/test_daily_bolao_service.py`

**Interfaces:**
- Consumes: `GameScorer`; `tournament_service.{create_tournament, add_game, open_tournament}`; `GameRepository.list_scheduled_in_window`, `TournamentRepository.{daily_auto_for, list_games}`, `PlayerRepository.list_all`; `tigrinho.ai.daily_bolao.{build_scoring_prompt, parse_scoring, sanitize_name, GameInterestCriteria}`; `tigrinho.domain.daily_bolao.{Candidate, InterestCriteria, interest, rank_and_select, local_day_window_utc}`; `tigrinho.ai.prompt.GameInfo`.
- Produces:
  - `DailyBolaoError(Exception)`
  - `DailyBolaoResult(status: Literal["created", "skipped"], reason: str, tournament: Tournament | None, games: tuple[Game, ...], mentions: tuple[tuple[int, str], ...])`
  - `async def create_daily_bolao(session_factory, scorer, settings, *, now: datetime, target_date: date) -> DailyBolaoResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily_bolao_service.py`:

```python
"""Tests for the daily-bolãozinho orchestration service (§24)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from tigrinho.ai.daily_bolao import DailyBolaoScoring, GameInterestCriteria, GameInterestScore
from tigrinho.daily_bolao_service import DailyBolaoError, create_daily_bolao
from tigrinho.db.models import (
    Game,
    GameStatus,
    Player,
    Stage,
    TournamentStatus,
    utcnow,
)
from tigrinho.db.repositories import TournamentRepository

# America/Sao_Paulo (UTC-3): 2026-06-21 local day == [03:00 UTC 06-21, 03:00 UTC 06-22).
_TARGET = date(2026, 6, 21)
_NOW = datetime(2026, 6, 20, 21, 0)  # the evening before
_IN_WINDOW = datetime(2026, 6, 21, 16, 0)  # 13:00 local, inside the day


def _crit(**over: bool) -> dict[str, bool]:
    base = dict(
        decisive=False,
        quality_matchup=False,
        rivalry_or_storyline=False,
        star_power=False,
        competitive_balance=False,
        goal_potential=False,
    )
    base.update(over)
    return base


class FakeScorer:
    """A GameScorer returning canned scoring JSON (or a raw string / raising an error)."""

    def __init__(
        self,
        *,
        scores: dict[int, dict[str, bool]] | None = None,
        name: str = "Dois jogões",
        raw: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._scores = scores or {}
        self._name = name
        self._raw = raw
        self._exc = exc
        self.calls = 0

    async def score_games(self, *, system_instruction: str, user_content: str) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        if self._raw is not None:
            return self._raw
        items = [
            GameInterestScore(fixture_id=fid, criteria=GameInterestCriteria(**crit))
            for fid, crit in self._scores.items()
        ]
        return DailyBolaoScoring(name=self._name, scores=items).model_dump_json()


def _seed_game(app_context, fid: int, kickoff: datetime) -> None:
    with app_context.session_factory() as s:
        s.add(
            Game(
                fixture_id=fid,
                match_hash=f"h{fid}",
                stage=Stage.GROUP,
                home_team_id=fid * 10,
                home_team_name=f"Home{fid}",
                away_team_id=fid * 10 + 1,
                away_team_name=f"Away{fid}",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
            )
        )
        s.commit()


def _seed_player(app_context, telegram_id: int) -> None:
    with app_context.session_factory() as s:
        s.add(Player(telegram_id=telegram_id, display_name=f"P{telegram_id}"))
        s.commit()


async def test_creates_and_opens_top_two(app_context) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 13, 0))
    _seed_game(app_context, 2, datetime(2026, 6, 21, 16, 0))
    _seed_game(app_context, 3, datetime(2026, 6, 21, 19, 0))
    _seed_player(app_context, 555)
    scorer = FakeScorer(
        scores={
            1: _crit(decisive=True),  # interest 1
            2: _crit(decisive=True, quality_matchup=True, star_power=True),  # interest 3
            3: _crit(decisive=True, quality_matchup=True),  # interest 2
        },
        name="Clássicos da Quarta",
    )

    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )

    assert result.status == "created"
    assert result.reason == ""
    assert scorer.calls == 1
    assert result.tournament is not None
    with app_context.session_factory() as s:
        t = TournamentRepository(s).get(result.tournament.id)
        assert t is not None
        assert t.status is TournamentStatus.OPEN
        assert t.name == "Clássicos da Quarta"
        assert t.entry_price_cents == app_context.settings.daily_bolao_entry_price_cents
        assert t.created_by == app_context.settings.admin_user_id
        assert t.auto_created_for == _TARGET
        # top 2 by interest: fixtures 2 (3) and 3 (2)
        assert {g.fixture_id for g in TournamentRepository(s).list_games(t.id)} == {2, 3}
    # one known player → at least one DM recipient passed back to the job
    assert 555 in {tid for tid, _ in result.mentions}


async def test_single_game_day_creates_one_game_pool(app_context) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(scores={1: _crit(goal_potential=True)})
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "created"
    with app_context.session_factory() as s:
        assert len(TournamentRepository(s).list_games(result.tournament.id)) == 1


async def test_zero_fixtures_skips_without_calling_scorer(app_context) -> None:
    scorer = FakeScorer(scores={})
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "skipped"
    assert result.reason == "no fixtures"
    assert scorer.calls == 0


async def test_idempotent_skip_when_already_created(app_context) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    with app_context.session_factory() as s:
        t = TournamentRepository(s).create(name="já existe", entry_price_cents=1000, created_by=1)
        t.auto_created_for = _TARGET
        s.commit()
    scorer = FakeScorer(scores={1: _crit(decisive=True)})
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "skipped"
    assert result.reason == "exists"
    assert scorer.calls == 0  # short-circuits before Gemini


async def test_blank_name_falls_back_to_dated_name(app_context) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(scores={1: _crit(decisive=True)}, name="  [1]  ")
    result = await create_daily_bolao(
        app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
    )
    assert result.status == "created"
    assert result.tournament.name == "Bolãozinho do dia 21/06"


async def test_scorer_error_raises_and_creates_nothing(app_context) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(exc=RuntimeError("gemini down"))
    with pytest.raises(RuntimeError):
        await create_daily_bolao(
            app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
        )
    with app_context.session_factory() as s:
        assert TournamentRepository(s).daily_auto_for(_TARGET) is None


async def test_only_hallucinated_ids_raises_no_fallback(app_context) -> None:
    _seed_game(app_context, 1, datetime(2026, 6, 21, 16, 0))
    scorer = FakeScorer(scores={999: _crit(decisive=True)})  # 999 not a candidate
    with pytest.raises(DailyBolaoError):
        await create_daily_bolao(
            app_context.session_factory, scorer, app_context.settings, now=_NOW, target_date=_TARGET
        )
    with app_context.session_factory() as s:
        assert TournamentRepository(s).daily_auto_for(_TARGET) is None
```

> Uses the existing `app_context` fixture (from `tests/conftest.py`) — the same one `tests/test_palpite_job.py` uses; it supplies a temp-DB `AppContext` whose `Settings` has the daily-bolão defaults (enabled stays `False`; the service does not gate on it — the job does). If the fixture's `admin_user_id` or `daily_bolao_entry_price_cents` differ, the assertions read them from `app_context.settings` rather than hard-coding.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_bolao_service.py -v`
Expected: FAIL (`No module named 'tigrinho.daily_bolao_service'`).

- [ ] **Step 3: Implement the service**

Create `tigrinho/daily_bolao_service.py`:

```python
"""Daily AI-curated bolãozinho orchestration (COMPLETION.md §24).

Telegram-free: query tomorrow's SCHEDULED games, ask the (separate) Gemini scorer to grade each
on six binary criteria, rank by count-of-trues in pure domain code, and create + open a
bolãozinho over the best ≤2. Network (the Gemini call) is async; the SQLite reads/writes are
synchronous (the project's split). There is NO fallback: a Gemini/parse failure or zero usable
picks raises, and the caller (the job) DMs the admin. Idempotency is guaranteed by the UNIQUE
``auto_created_for`` column (pre-check + IntegrityError on commit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tigrinho import tournament_service as svc
from tigrinho.ai.base import GameScorer
from tigrinho.ai.daily_bolao import (
    GameInterestCriteria,
    build_scoring_prompt,
    parse_scoring,
    sanitize_name,
)
from tigrinho.ai.prompt import GameInfo
from tigrinho.config import Settings
from tigrinho.db.models import Game, Tournament
from tigrinho.db.repositories import GameRepository, PlayerRepository, TournamentRepository
from tigrinho.domain.daily_bolao import (
    Candidate,
    InterestCriteria,
    interest,
    local_day_window_utc,
    rank_and_select,
)
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.daily_bolao_service")

_MAX_GAMES = 2


class DailyBolaoError(Exception):
    """A genuine failure (no fallback) — the caller DMs the admin and creates nothing."""


@dataclass(frozen=True, slots=True)
class DailyBolaoResult:
    """Outcome of one daily run; ``games``/``mentions`` feed the open announcement."""

    status: Literal["created", "skipped"]
    reason: str = ""
    tournament: Tournament | None = None
    games: tuple[Game, ...] = ()
    mentions: tuple[tuple[int, str], ...] = field(default=())


def _to_domain(c: GameInterestCriteria) -> InterestCriteria:
    return InterestCriteria(
        decisive=c.decisive,
        quality_matchup=c.quality_matchup,
        rivalry_or_storyline=c.rivalry_or_storyline,
        star_power=c.star_power,
        competitive_balance=c.competitive_balance,
        goal_potential=c.goal_potential,
    )


def _skipped(reason: str) -> DailyBolaoResult:
    return DailyBolaoResult(status="skipped", reason=reason)


async def create_daily_bolao(
    session_factory: sessionmaker[Session],
    scorer: GameScorer,
    settings: Settings,
    *,
    now: datetime,
    target_date: date,
) -> DailyBolaoResult:
    """Create + auto-open the daily AI bolãozinho for ``target_date`` (or skip / raise)."""
    start_utc, end_utc = local_day_window_utc(target_date, settings.tzinfo)

    # Session 1: idempotency pre-check + read candidates.
    with session_factory() as session:
        if TournamentRepository(session).daily_auto_for(target_date) is not None:
            return _skipped("exists")
        games = GameRepository(session).list_scheduled_in_window(start_utc, end_utc)
        candidates = [Candidate(fixture_id=g.fixture_id, kickoff_utc=g.kickoff_utc) for g in games]
        infos = [
            GameInfo(
                fixture_id=g.fixture_id,
                home_team=g.home_team_name,
                away_team=g.away_team_name,
                kickoff_local=g.kickoff_local,
                stage=g.stage,
            )
            for g in games
        ]

    if not candidates:
        return _skipped("no fixtures")

    # Gemini call (async, outside any session). Raises on failure → no fallback.
    system_instruction, user_content = build_scoring_prompt(infos)
    raw = await scorer.score_games(
        system_instruction=system_instruction, user_content=user_content
    )
    batch = parse_scoring(raw)
    scores = {s.fixture_id: interest(_to_domain(s.criteria)) for s in batch.scores}
    picks = rank_and_select(candidates, scores, limit=_MAX_GAMES)
    if not picks:
        raise DailyBolaoError("Gemini não pontuou nenhum jogo válido")
    _log.info(
        "daily_bolao_scored", candidates=len(candidates), scored=len(scores), picks=picks
    )
    name = sanitize_name(batch.name) or f"Bolãozinho do dia {target_date:%d/%m}"

    # Session 2: create + open. The UNIQUE constraint + IntegrityError is the real idempotency guard.
    with session_factory() as session:
        tournament = svc.create_tournament(
            session,
            name=name,
            entry_price_cents=settings.daily_bolao_entry_price_cents,
            created_by=settings.admin_user_id,
        )
        tournament.auto_created_for = target_date
        for fixture_id in picks:
            svc.add_game(session, tournament, fixture_id, now=now)
        svc.open_tournament(
            session, tournament, now=now, splitwise_enabled=settings.splitwise_enabled
        )
        out_games = tuple(TournamentRepository(session).list_games(tournament.id))
        mentions = tuple(
            (p.telegram_id, p.display_name) for p in PlayerRepository(session).list_all()
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return _skipped("exists")

    return DailyBolaoResult(
        status="created", tournament=tournament, games=out_games, mentions=mentions
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_daily_bolao_service.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/daily_bolao_service.py tests/test_daily_bolao_service.py
git commit -m "feat(service): create_daily_bolao orchestrator, no-fallback (§24)"
```

---

## Task 8: Job + wiring

**Files:**
- Create: `tigrinho/bot/daily_bolao_job.py`
- Modify: `tigrinho/bot/runtime.py`, `tigrinho/__main__.py`, `tigrinho/bot/app.py`
- Test: `tests/test_daily_bolao_job.py`

**Interfaces:**
- Consumes: `create_daily_bolao`, `announce_open`, `AppContext.game_scorer`, `notify_admin`, `get_app_context`.
- Produces:
  - `DAILY_BOLAO_JOB_NAME = "daily_bolao"`
  - `async def daily_bolao_job(context) -> None`
  - `def schedule_daily_bolao_job(job_queue, settings) -> None`
  - `AppContext.game_scorer: GameScorer | None`
  - `make_game_scorer(settings) -> GameScorer | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily_bolao_job.py`:

```python
"""Tests for the daily-bolãozinho run_daily job (§24)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ContextTypes

from tigrinho.ai.daily_bolao import DailyBolaoScoring, GameInterestCriteria, GameInterestScore
from tigrinho.bot.daily_bolao_job import DAILY_BOLAO_JOB_NAME, daily_bolao_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AppContext
from tigrinho.db.models import Game, GameStatus, Stage
from tigrinho.db.repositories import TournamentRepository
from tigrinho.domain.daily_bolao import local_day_window_utc


def _ctx(app_context: AppContext) -> tuple[ContextTypes.DEFAULT_TYPE, AsyncMock]:
    ctx = MagicMock()
    ctx.application.bot_data = {APP_CONTEXT_KEY: app_context}
    bot = AsyncMock()
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx), bot


def _crit() -> GameInterestCriteria:
    return GameInterestCriteria(
        decisive=True,
        quality_matchup=True,
        rivalry_or_storyline=False,
        star_power=True,
        competitive_balance=False,
        goal_potential=True,
    )


class FakeScorer:
    def __init__(self, fixture_ids: list[int], *, exc: Exception | None = None) -> None:
        self._fixture_ids = fixture_ids
        self._exc = exc
        self.calls = 0

    async def score_games(self, *, system_instruction: str, user_content: str) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        scores = [GameInterestScore(fixture_id=f, criteria=_crit()) for f in self._fixture_ids]
        return DailyBolaoScoring(name="Bolão de Teste", scores=scores).model_dump_json()


def _tomorrow_kickoff(app_context: AppContext) -> datetime:
    tz = app_context.settings.tzinfo
    target = (datetime.now(tz) + timedelta(days=1)).date()
    start, end = local_day_window_utc(target, tz)
    return start + (end - start) / 2  # midday-ish, safely inside the window


def _seed_game(app_context: AppContext, fid: int, kickoff: datetime) -> None:
    with app_context.session_factory() as s:
        s.add(
            Game(
                fixture_id=fid,
                match_hash=f"h{fid}",
                stage=Stage.GROUP,
                home_team_id=fid * 10,
                home_team_name=f"Home{fid}",
                away_team_id=fid * 10 + 1,
                away_team_name=f"Away{fid}",
                kickoff_utc=kickoff,
                kickoff_local=kickoff,
                status=GameStatus.SCHEDULED,
            )
        )
        s.commit()


async def test_noop_when_no_scorer(app_context: AppContext) -> None:
    ctx, bot = _ctx(dataclasses.replace(app_context, game_scorer=None))
    await daily_bolao_job(ctx)
    bot.send_message.assert_not_awaited()


async def test_creates_and_announces(app_context: AppContext) -> None:
    kickoff = _tomorrow_kickoff(app_context)
    _seed_game(app_context, 1, kickoff)
    _seed_game(app_context, 2, kickoff + timedelta(hours=3))
    scorer = FakeScorer([1, 2])
    ctx, bot = _ctx(dataclasses.replace(app_context, game_scorer=scorer))

    await daily_bolao_job(ctx)

    assert scorer.calls == 1
    # group announcement was posted
    chat_ids = [c.kwargs.get("chat_id") for c in bot.send_message.await_args_list]
    assert app_context.settings.group_chat_id in chat_ids
    with app_context.session_factory() as s:
        target = (datetime.now(app_context.settings.tzinfo) + timedelta(days=1)).date()
        assert TournamentRepository(s).daily_auto_for(target) is not None


async def test_failure_dms_admin_and_does_not_crash(app_context: AppContext) -> None:
    _seed_game(app_context, 1, _tomorrow_kickoff(app_context))
    scorer = FakeScorer([1], exc=RuntimeError("boom"))
    ctx, bot = _ctx(dataclasses.replace(app_context, game_scorer=scorer))

    await daily_bolao_job(ctx)  # must NOT raise

    admin_id = app_context.settings.admin_user_id
    assert any(c.kwargs.get("chat_id") == admin_id for c in bot.send_message.await_args_list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_bolao_job.py -v`
Expected: FAIL (`No module named 'tigrinho.bot.daily_bolao_job'` / `AppContext` has no `game_scorer`).

- [ ] **Step 3: Add `game_scorer` to `AppContext`**

In `tigrinho/bot/runtime.py`, add the import and a field. Add to the imports near the other AI/optional ones:

```python
from tigrinho.ai.base import GameScorer
```

Add the field to the `AppContext` dataclass right after `palpite_generator`:

```python
    # Daily-bolãozinho game-interest scorer (§24); None when the feature is disabled.
    game_scorer: GameScorer | None = None
```

- [ ] **Step 4: Create the job**

Create `tigrinho/bot/daily_bolao_job.py`:

```python
"""Daily AI-curated bolãozinho job (COMPLETION.md §24).

A ``JobQueue.run_daily`` job at ``daily_bolao_time`` (default 18:00 local, the evening before). It
picks the best ≤2 of tomorrow's fixtures via the Gemini scorer and auto-opens a bolãozinho over
them — posting to the group + DMing players exactly like ``/bolaozinho_abrir``. There is no
fallback: any failure DMs the admin and creates nothing. One bad cycle never kills the bot (§14).
The job is only scheduled when ``daily_bolao_enabled`` (see ``app.py``); the ``game_scorer is None``
guard is defensive.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.runtime import get_app_context
from tigrinho.bot.tournament_handlers import announce_open
from tigrinho.config import Settings
from tigrinho.daily_bolao_service import create_daily_bolao
from tigrinho.db.models import utcnow
from tigrinho.domain.text_pt import escape
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.daily_bolao_job")

DAILY_BOLAO_JOB_NAME = "daily_bolao"


async def daily_bolao_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create + auto-open tomorrow's AI bolãozinho (§24). One bad cycle must not kill the bot."""
    app_context = get_app_context(context.application)
    scorer = app_context.game_scorer
    if scorer is None:
        _log.info("daily_bolao_skipped", reason="no game scorer configured")
        return

    settings = app_context.settings
    target_date = (datetime.now(settings.tzinfo) + timedelta(days=1)).date()
    try:
        result = await create_daily_bolao(
            app_context.session_factory,
            scorer,
            settings,
            now=utcnow(),
            target_date=target_date,
        )
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("daily_bolao_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Bolãozinho diário falhou: <code>{escape(str(exc))}</code>",
        )
        return

    if result.status == "created" and result.tournament is not None:
        await announce_open(
            context, settings, result.tournament, list(result.games), list(result.mentions)
        )
        _log.info("daily_bolao_created", tournament_id=result.tournament.id)
    else:
        _log.info("daily_bolao_skipped", reason=result.reason)


def schedule_daily_bolao_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the daily bolãozinho creation at ``daily_bolao_time`` in the timezone (§24)."""
    run_time = settings.daily_bolao_time_obj.replace(tzinfo=settings.tzinfo)
    job_queue.run_daily(daily_bolao_job, time=run_time, name=DAILY_BOLAO_JOB_NAME)
```

- [ ] **Step 5: Wire the factory + AppContext construction**

In `tigrinho/__main__.py`, add the import for `GeminiGameScorer` (alongside the existing `GeminiPalpiteGenerator` import) and the `GameScorer` type, then add the factory after `make_palpite_generator`:

```python
def make_game_scorer(settings: Settings) -> GameScorer | None:
    """Build the daily-bolãozinho Gemini scorer, or None when no key is configured (§24)."""
    if not settings.gemini_api_key:
        return None
    return GeminiGameScorer(api_key=settings.gemini_api_key, model=settings.gemini_model)
```

Add `game_scorer=make_game_scorer(settings),` to the `AppContext(...)` construction (after `palpite_generator=...`):

```python
        palpite_generator=make_palpite_generator(settings),
        game_scorer=make_game_scorer(settings),
        splitwise_client=make_splitwise_client(settings),
```

(Ensure the file imports `GameScorer` from `tigrinho.ai.base` and `GeminiGameScorer` from `tigrinho.ai.gemini`.)

- [ ] **Step 6: Schedule the job (only when enabled)**

In `tigrinho/bot/app.py`, add the import:

```python
from tigrinho.bot.daily_bolao_job import schedule_daily_bolao_job
```

In `post_init`, after `schedule_sweep_job(...)`, add:

```python
        if app_context.settings.daily_bolao_enabled:
            schedule_daily_bolao_job(application.job_queue, app_context.settings)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_daily_bolao_job.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/bot/daily_bolao_job.py tigrinho/bot/runtime.py tigrinho/__main__.py tigrinho/bot/app.py tests/test_daily_bolao_job.py
git commit -m "feat(bot): daily_bolao job + scorer wiring + scheduling (§24)"
```

---

## Task 9: Docs — COMPLETION.md §24, /ajuda, PROGRESS.md

**Files:**
- Modify: `COMPLETION.md`, `PROGRESS.md`, the `/ajuda` help text source, its test.

**Interfaces:** none (documentation + user-visible help text per the maintenance rule).

- [ ] **Step 1: Locate the `/ajuda` text and write the failing test**

Run: `rg -n "ajuda" tigrinho/bot/help_handlers.py tigrinho/domain/text_pt.py` to find the function that builds the help body (the command handler is in `help_handlers.py`; the text may live in `text_pt.py`).

Find the test for it: `rg -n "ajuda|help" tests/test_text_pt.py tests/test_help*.py`. Add an assertion that the help text mentions the daily bolãozinho. Example (adapt the call to the actual help-text function name found above):

```python
def test_help_mentions_daily_bolao() -> None:
    text = help_text()  # the actual builder located in Step 1
    assert "todo dia" in text.lower() or "bolãozinho do dia" in text.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_text_pt.py -k daily_bolao -v` (or the file where you added it)
Expected: FAIL (substring not present).

- [ ] **Step 3: Add the help line**

In the located help-text builder, add one line to the relevant section (HTML parse mode — keep tags balanced):

```
🤖 <b>Bolãozinho do dia:</b> todo dia, à noite, eu escolho os melhores jogos do dia seguinte e abro um bolãozinho automático aqui no grupo.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_text_pt.py -k daily_bolao -v`
Expected: PASS.

- [ ] **Step 5: Add COMPLETION.md §24**

Append a new section `## 24. Daily AI-curated bolãozinho` summarizing: the evening `run_daily` job; the independent Gemini `GameScorer` flow; the six binary criteria (list them) with interest = count-of-trues computed in pure code; up-to-2 selection (skip only on a 0-game day); auto-open identical to `/bolaozinho_abrir`; fixed `daily_bolao_entry_price_cents`; `created_by=admin_user_id`; idempotency via UNIQUE `auto_created_for`; the no-fallback policy (DM admin on failure); and the `daily_bolao_enabled` + `GEMINI_API_KEY` gate. Reference the spec file.

- [ ] **Step 6: Update PROGRESS.md**

Add a row/section for §24 marking the tasks done (config, domain, AI models, scorer, DB+migration, announce refactor, service, job+wiring, docs), each ticked.

- [ ] **Step 7: Run all gates + commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add COMPLETION.md PROGRESS.md tigrinho/ tests/
git commit -m "docs(bolaozinho): COMPLETION §24 + /ajuda + PROGRESS for daily AI pool (§11, §24)"
```

---

## Self-Review

**Spec coverage:**
- §2.1 job/schedule → Task 8. §2.2 DST-safe window → Task 2 (`local_day_window_utc`). §2.3 candidates → Task 5 (`list_scheduled_in_window`). §2.4 independent flow → Task 4. §2.5 binary scoring → Tasks 2+3. §2.6 up-to-2 → Task 2 (`rank_and_select`). §2.7 auto-open → Tasks 6+7. §2.8 created_by → Task 7. §2.9 fixed price → Tasks 1+7. §2.10 name + fallback → Tasks 3+7. §2.11 no fallback → Task 7. §2.12 idempotency UNIQUE → Tasks 5+7. §2.13 gate + fail-fast → Tasks 1+8. §5 models/client → Tasks 3+4. §7 announce extraction → Task 6. §9 model/migration/repos → Task 5. §10 config → Task 1. §11 docs → Task 9. §12 tests → covered per task. §13 edge cases → service tests (Task 7). All sections map to a task.

**Placeholder scan:** No "TBD/TODO". The only lookups are in Task 6 (reuse the existing open-test seed helper) and Task 9 (locate the help-text builder via `rg`) — both give the exact content to add; the unknown is only a local function name, resolved by the shown `rg` command.

**Type consistency:** `GameInterestCriteria`/`GameInterestScore`/`DailyBolaoScoring` (Task 3) are consumed unchanged in Tasks 7–8. `Candidate`/`InterestCriteria`/`interest`/`rank_and_select`/`local_day_window_utc` (Task 2) match their uses in Task 7. `create_daily_bolao` signature (Task 7) matches its call in Task 8. `announce_open` signature (Task 6) matches its call in Task 8. `score_games(*, system_instruction, user_content) -> str` is identical across the protocol (Task 4), client (Task 4), and both `FakeScorer`s (Tasks 7–8). `DailyBolaoResult` fields (`status`, `reason`, `tournament`, `games`, `mentions`) are read consistently in Task 8.
