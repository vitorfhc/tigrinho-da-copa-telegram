# Daily AI Bolãozinho — Design Spec

**Date:** 2026-06-20
**Status:** Draft for user review (pre-planning)
**Feature:** Daily AI-curated bolãozinho (becomes COMPLETION.md §24)

> **Naming.** As with §22/§23, the UI says **"bolãozinho"** (pt-BR) and the internal code keeps English
> identifiers (`tournament*` tables/models). This feature adds a **second, independent Gemini flow** —
> a *game-interest scorer* — that is deliberately **not** the existing `/palpite` generator (§20).

> **Grounding (MANDATORY before coding — §2/§11).** This feature mostly composes already-grounded
> surfaces: the google-genai SDK call shape (`genai.Client(...).aio.models.generate_content` with
> Google-Search grounding) verified in `tigrinho/ai/gemini.py`, and `JobQueue.run_daily(...)` used by
> every existing daily job. **Re-verify the google-genai `generate_content` config arguments and
> `run_daily` signature against the live docs when writing the new client/job**, and keep the doc-url
> comment that already heads `gemini.py`. If live docs disagree with this spec, live docs win and this
> spec + COMPLETION.md must be updated.

---

## 1. Summary

Each evening the bot looks at **tomorrow's** World Cup fixtures, asks a **dedicated Gemini flow** to
grade every candidate game on a fixed set of **binary (yes/no) "is this interesting to bet on?"
criteria**, ranks the games by how many criteria came back `true`, and **creates + auto-opens** a
bolãozinho (§22) over the **top 2** (or the single game, on a one-game day). Opening is byte-identical to
a manual `/bolaozinho_abrir`: it posts to the group, @-mentions everyone, DMs all players, and — when
Splitwise is on — stamps `splitwise_mode=AUTO` (§23).

The feature is **opt-in and dormant** unless `daily_bolao_enabled` is set in `config.yaml` **and** a
Gemini key is configured — mirroring the `/palpite` gate but adding an explicit enable flag, because
auto-opening a *real-money* pot every day should never happen by accident.

**There is no heuristic fallback.** If Gemini fails or returns nothing usable, the bot creates nothing
and **DMs the admin that it failed** (the only cases that pass silently are: feature off, zero fixtures
tomorrow, or a daily bolãozinho already created for that date).

## 2. Product decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | What runs | A new daily **`run_daily` job** at `daily_bolao_time` (default **18:00**, configured timezone), scheduled in `app.py:post_init` next to the existing jobs. |
| 2 | Day selected | **Tomorrow**, as a calendar day in the configured timezone (`America/Sao_Paulo`). The window is `[tomorrow 00:00 local, +24h)`, converted to naive UTC to match `Game.kickoff_utc`. |
| 3 | Candidate games | `SCHEDULED` games whose `kickoff_utc` falls in tomorrow's local-day window. (Fixtures are already in the DB: the 06:00 sync pulls a 48h window, so tomorrow is always present by 18:00.) |
| 4 | Selection engine | **A new, independent Gemini flow** (`GameScorer` protocol + `GeminiGameScorer` client). **Not** the `/palpite` `PalpiteGenerator`. |
| 5 | Scoring | **Binary only.** Per game, Gemini returns **6 boolean criteria**; the interest score is `sum(criteria)` (0–6), **computed in pure code — the model never emits a number**. This is the only allowed "0–N score": it is *derived from multiple binary grades*. |
| 6 | How many games | **Up to 2.** Rank by `(interest desc, kickoff asc)`, take the top 2; on a one-game day, use that one game; **skip only when there are 0 candidates**. |
| 7 | Activation | **Auto-open**, fully automatic (no human in the loop). Real-money, Splitwise `AUTO` when the Splitwise feature is enabled. |
| 8 | Creator | `created_by = settings.admin_user_id`, so the admin can `/bolaozinho_cancelar` or otherwise manage it. |
| 9 | Entry price | **Fixed** `daily_bolao_entry_price_cents` in `config.yaml` (default `1000` = R$ 10,00), frozen at first join like any bolãozinho. |
| 10 | Name | **Gemini-proposed** (short, fun pt-BR), sanitized; deterministic fallback `Bolãozinho do dia DD/MM` only when the proposed name is blank/whitespace. (This is a *name* fallback, not a *selection* fallback.) |
| 11 | Failure policy | **No selection fallback.** Gemini error / unparseable response / zero usable scores on a non-empty day → **create nothing, DM admin**. |
| 12 | Idempotency | New nullable `tournaments.auto_created_for` (Date). The job skips if a daily-auto bolãozinho already exists for the target date. Also labels "this is the daily one." |
| 13 | Feature gate | Active iff `daily_bolao_enabled` (`config.yaml`) is `true`. Startup **fails fast** if enabled without `GEMINI_API_KEY`. When disabled, the job is **not scheduled** at all. |

## 3. Module layout (new + touched)

```
tigrinho/ai/base.py            (+) GameScorer Protocol
tigrinho/ai/gemini.py          (+) GeminiGameScorer client (new flow, parallel to GeminiPalpiteGenerator)
tigrinho/ai/daily_bolao.py     (NEW) criteria/score pydantic models, prompt builder, parser,
                                     pure rank_and_select() + interest()
tigrinho/daily_bolao_service.py (NEW) async orchestrator (query → score → create+open)
tigrinho/bot/daily_bolao_job.py (NEW) run_daily job + scheduler + failure DM
tigrinho/bot/tournament_announce.py (~) extract shared "open + announce" helper
tigrinho/bot/tournament_handlers.py (~) cmd_abrir calls the shared helper (no behaviour change)
tigrinho/bot/app.py            (~) schedule the new job (guarded by daily_bolao_enabled)
tigrinho/bot/runtime.py        (~) AppContext.game_scorer: GameScorer | None
tigrinho/__main__.py           (~) make_game_scorer(settings) factory + wiring
tigrinho/config.py             (~) daily_bolao_enabled / _time / _entry_price_cents + validation
tigrinho/db/models.py          (~) Tournament.auto_created_for: Mapped[date | None]
tigrinho/db/repositories.py    (~) GameRepository.list_scheduled_in_window(); TournamentRepository
                                     .daily_auto_for(date) / set auto_created_for
alembic/versions/*             (NEW) append-only migration: add tournaments.auto_created_for
config.example.yaml            (~) document the three new keys
COMPLETION.md / PROGRESS.md / /ajuda (~) §24 + help line
```

## 4. Data flow

```
18:00 local ── daily_bolao_job(context)
  ├─ guard: scorer is not None  (else return; defensive — job isn't scheduled when disabled)
  ├─ now_local = now(tz); target_date = (now_local + 1d).date()
  ├─ service.create_daily_bolao(session_factory, scorer, settings, now=utcnow(), target_date=…)
  │     ├─ if TournamentRepository.daily_auto_for(target_date) is not None: return SKIPPED  (idempotent)
  │     ├─ candidates = GameRepository.list_scheduled_in_window(start_utc, end_utc)
  │     ├─ if not candidates: return SKIPPED  (0-game day — silent)
  │     ├─ (system, user) = build_scoring_prompt(candidates)
  │     ├─ raw = await scorer.score_games(system_instruction=system, user_content=user)   # may raise
  │     ├─ scoring = parse_scoring(raw)                                                    # may raise
  │     ├─ picks = rank_and_select(candidate_ids, scoring)   # pure; intersect, rank, top-2
  │     ├─ if not picks: raise DailyBolaoError("Gemini não pontuou nenhum jogo válido")    # NO fallback
  │     ├─ name = sanitize(scoring.name) or f"Bolãozinho do dia {target_date:%d/%m}"
  │     ├─ t = create_tournament(name=name, entry_price_cents=price, created_by=admin_id)
  │     ├─ t.auto_created_for = target_date
  │     ├─ for fid in picks: add_game(t, fid, now=now)
  │     ├─ open_tournament(t, now=now, splitwise_enabled=settings.splitwise_enabled)
  │     ├─ games = list_games(t.id); mentions = all_player_mentions(session)
  │     ├─ session.commit()
  │     └─ return CREATED(tournament=t, games=games, mentions=mentions)
  ├─ on CREATED: await open_announce(context, settings, tournament, games, mentions)  # shared helper
  └─ except Exception as exc: notify_admin(bot, admin_id, "Bolãozinho diário falhou: …")  # DM, no crash
```

## 5. The new Gemini flow

### 5.1 Protocol (`tigrinho/ai/base.py`)
```python
@runtime_checkable
class GameScorer(Protocol):
    """Grades candidate games on binary interest criteria; returns raw model text (JSON)."""
    async def score_games(self, *, system_instruction: str, user_content: str) -> str: ...
```
Independent from `PalpiteGenerator` — distinct name, distinct method, distinct schema. Tests inject a
fake; production injects `GeminiGameScorer`.

### 5.2 Client (`tigrinho/ai/gemini.py`)
A new class parallel to `GeminiPalpiteGenerator`, sharing the SDK/grounding/thinking conventions already
documented at the top of the file:
```python
class GeminiGameScorer:
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
            model=self._model, contents=user_content, config=config,
        )
        text = response.text
        if not text:
            raise ValueError("Gemini returned an empty game-scoring response")
        return text
```
Reuses `settings.gemini_model` (no new model setting).

### 5.3 Binary scoring model (`tigrinho/ai/daily_bolao.py`)
Six betting-relevant booleans; each is independently gradeable with web grounding:

| criterion | `true` when… |
|---|---|
| `decisive` | knockout, or the result decides group qualification / seeding |
| `quality_matchup` | both sides are strong / notable national teams |
| `rivalry_or_storyline` | historic rivalry or a compelling narrative |
| `star_power` | a globally famous player is likely to feature |
| `competitive_balance` | closely matched, genuinely hard to call |
| `goal_potential` | likely open / entertaining / high-scoring |

```python
class GameInterestCriteria(BaseModel):
    decisive: bool
    quality_matchup: bool
    rivalry_or_storyline: bool
    star_power: bool
    competitive_balance: bool
    goal_potential: bool

class GameInterestScore(BaseModel):
    fixture_id: int
    criteria: GameInterestCriteria

class DailyBolaoScoring(BaseModel):
    name: str
    scores: list[GameInterestScore]
```

**Derived interest (pure, in code — never from the model):**
```python
def interest(criteria: GameInterestCriteria) -> int:
    return sum((criteria.decisive, criteria.quality_matchup, criteria.rivalry_or_storyline,
                criteria.star_power, criteria.competitive_balance, criteria.goal_potential))
```

**Selection (pure, deterministic, ~100% covered):**
```python
def rank_and_select(
    candidates: Sequence[Candidate],          # (fixture_id, kickoff_utc) for tomorrow, in kickoff order
    scoring: DailyBolaoScoring,
    *, limit: int = 2,
) -> list[int]:
    by_id = {c.fixture_id: c for c in candidates}
    scored = [
        (interest(s.criteria), by_id[s.fixture_id].kickoff_utc, s.fixture_id)
        for s in scoring.scores
        if s.fixture_id in by_id          # anti-hallucination: drop ids we didn't ask about
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))   # interest desc, then earliest kickoff
    return [fid for _, _, fid in scored[:limit]]
```
Returns `[]` when the model scored none of the real candidates → caller raises (no fallback).

### 5.4 Prompt (`build_scoring_prompt(candidates) -> tuple[str, str]`)
- **System instruction:** "You are a World Cup 2026 football analyst. For EACH fixture, grade the six
  yes/no criteria below as booleans. Do **not** output any numeric score or ranking — only the booleans.
  Also propose one short, fun Brazilian-Portuguese name for a daily betting pool over the best of these
  games. Use Google Search for current form, stakes, and lineups. Output JSON only." — followed by the
  exact criterion definitions and the JSON shape.
- **User content:** one line per candidate: `fixture_id=<id> | <home> x <away> | <kickoff_local> |
  <group|knockout>` (mirrors `prompt.py`).
- `parse_scoring(text)` reuses the `extract_json` helper pattern from `ai/schemas.py`, then validates
  against `DailyBolaoScoring`.

## 6. Service (`tigrinho/daily_bolao_service.py`)
```python
class DailyBolaoError(Exception): ...

@dataclass(frozen=True, slots=True)
class DailyBolaoResult:
    status: Literal["created", "skipped"]
    tournament: Tournament | None
    games: list[Game]
    mentions: list[Mention]            # same shape cmd_abrir passes to the announce helpers
    reason: str = ""                   # for "skipped" (e.g. "no fixtures", "already exists")

async def create_daily_bolao(
    session_factory: sessionmaker[Session],
    scorer: GameScorer,
    settings: Settings,
    *, now: datetime, target_date: date,
) -> DailyBolaoResult:
    ...
```
- The Gemini call is `await`ed **outside** the DB session (network = async; SQLite = sync), matching
  `palpite_service`. DB reads (candidates) happen first in a short session; the create/open happens in a
  second session after scoring.
- Raises `DailyBolaoError` (or lets a Gemini/parse exception propagate) for genuine failures; returns a
  `"skipped"` result for the three silent cases. The **job** translates a raised exception into the admin
  DM.

## 7. Shared "open + announce" extraction
`cmd_abrir` currently does, after `open_tournament(...)` + `commit()`:
`_post_open_announcement(...)`, `_broadcast_open_dm(...)`, then replies the admin card.
Extract the **group post + DM broadcast** (and `_all_player_mentions`) into
`tigrinho/bot/tournament_announce.py` as one coroutine, e.g.:
```python
async def announce_open(context, settings, tournament, games, mentions) -> None:
    await _post_open_announcement(context, settings, tournament, games, mentions)
    await _broadcast_open_dm(context, settings, tournament, games, mentions)
```
`cmd_abrir` calls it (its admin-card reply stays in the handler); the new job calls the same coroutine.
This guarantees an auto-opened bolãozinho is indistinguishable from a manually-opened one and removes
duplication. **No behavioural change to `/bolaozinho_abrir`.**

## 8. Scheduling, gating & failure handling (`tigrinho/bot/daily_bolao_job.py`)
```python
DAILY_BOLAO_JOB_NAME = "daily_bolao"

async def daily_bolao_job(context):
    app = get_app_context(context.application); s = app.settings
    if app.game_scorer is None:
        return                                   # defensive; job isn't scheduled when disabled
    try:
        target_date = (datetime.now(s.tzinfo) + timedelta(days=1)).date()
        result = await create_daily_bolao(app.session_factory, app.game_scorer, s,
                                           now=utcnow(), target_date=target_date)
        if result.status == "created":
            await announce_open(context, s, result.tournament, result.games, result.mentions)
    except Exception as exc:                      # one bad cycle never kills the bot (§14)
        _log.error("daily_bolao_failed", error=str(exc))
        await notify_admin(context.bot, s.admin_user_id,
                           f"⚠️ Bolãozinho diário falhou: {exc}")

def schedule_daily_bolao_job(job_queue, settings):
    run_time = settings.daily_bolao_time_obj.replace(tzinfo=settings.tzinfo)
    job_queue.run_daily(daily_bolao_job, time=run_time, name=DAILY_BOLAO_JOB_NAME)
```
`app.py:post_init` calls `schedule_daily_bolao_job(...)` **only when `settings.daily_bolao_enabled`**.

## 9. Data model & migration
Add to `Tournament` (`db/models.py`):
```python
auto_created_for: Mapped[date | None] = mapped_column(Date, nullable=True, default=None, index=True)
```
- **Append-only Alembic migration** adds the nullable column (default `NULL`); existing rows untouched.
- `TournamentRepository.daily_auto_for(target_date) -> Tournament | None` queries
  `auto_created_for == target_date` (any status — DRAFT/OPEN/FINISHED/CANCELLED all block re-creation for
  that day). The service sets `tournament.auto_created_for = target_date` right after `create_tournament`.
- `GameRepository.list_scheduled_in_window(start_utc, end_utc) -> list[Game]`: `status == SCHEDULED`,
  `start_utc <= kickoff_utc < end_utc`, ordered by `kickoff_utc`.

## 10. Config additions (`config.py` + `config.example.yaml`)
| field | type | default | source | notes |
|---|---|---|---|---|
| `daily_bolao_enabled` | `bool` | `False` | config.yaml | master gate; when off the job isn't scheduled |
| `daily_bolao_time` | `str` | `"18:00"` | config.yaml | local `HH:MM`; `daily_bolao_time_obj` property mirrors `palpite_time_obj` |
| `daily_bolao_entry_price_cents` | `int` | `1000` | config.yaml | R$ 10,00; must be `> 0` |

Startup validation (fail fast, §3): if `daily_bolao_enabled` then require `gemini_api_key` set **and**
`daily_bolao_entry_price_cents > 0`; otherwise raise a clear config error. `GEMINI_API_KEY` stays the only
secret (no new `.env` key). `make_game_scorer(settings)` returns `None` when `not gemini_api_key`.

## 11. Docs & maintenance (§11 rule)
- **COMPLETION.md §24** — new section documenting the feature, decisions table, and the binary-criteria
  list (source of truth).
- **/ajuda** — one line noting that a daily bolãozinho is opened automatically each evening over the next
  day's best games (no new command, but user-visible behaviour).
- **PROGRESS.md** — new milestone row, ticked as the increments land.
- **config.example.yaml** — the three new keys with comments.

## 12. Testing plan
| layer | file | what |
|---|---|---|
| pure | `tests/test_daily_bolao_pure.py` | `interest()` truth-table; `rank_and_select` ranking, tie-break by kickoff, `limit`, anti-hallucination drop, empty result → `[]`. **~100% line+branch.** |
| AI parse | same / `test_ai_*` | `parse_scoring` happy path, fenced JSON, prose-wrapped, schema violation → raises. |
| client | `tests/test_gemini_generator.py` | `GeminiGameScorer` via the existing `genai.Client` monkeypatch; asserts config (grounding + thinking) and empty-text → raises. |
| service | `tests/test_daily_bolao_service.py` | with a `FakeScorer` + in-memory DB: creates+opens correct games/price/name; idempotency skip; 0-game skip; **bad model → raises (no fallback, nothing created)**; respects `splitwise_enabled`→AUTO. |
| job | `tests/test_daily_bolao_job.py` | no-op when scorer `None`; success path calls `announce_open`; exception → `notify_admin` and no crash. |
| shared | `tests/test_tournament_handlers.py` | `cmd_abrir` still posts group + DM after the extraction (no regression). |
| config | `tests/test_config.py` | new fields + `daily_bolao_time_obj`; `enabled` without key → fail fast. |
| migration | `tests/test_migrations.py` | upgrade adds `auto_created_for`; existing rows `NULL`. |

`FakeScorer` mirrors `FakeGenerator`: configured with `{fixture_id: criteria-bools}` + a name, returns
canned JSON for `score_games`.

All four gates green: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`. The pure
`daily_bolao` selection/scoring functions stay I/O-free.

## 13. Edge cases
- **0 fixtures tomorrow** → silent skip (no post, no DM).
- **1 fixture** → single-game bolãozinho (still scored + named via Gemini).
- **Already created for the date** (rerun / redeploy at 18:00) → silent skip via `auto_created_for`.
- **Gemini scores only hallucinated ids** (none match candidates) → `picks == []` → raise → admin DM.
- **All criteria false for every game** → still picks top-2 by tie-break (earliest kickoff); a low-interest
  day still gets a pool (the user chose "up to 2; skip only on a 0-game day"; no interest threshold).
- **Blank proposed name** → `Bolãozinho do dia DD/MM`.
- **Splitwise off** → opens MANUAL like any bolãozinho; **on** → AUTO (join guard applies, §23).

## 14. Out of scope (YAGNI)
- No new command, keyboard wizard, or admin-review step (activation is fully automatic per the chosen
  design). The admin manages the result via existing `/bolaozinho_*` commands.
- No minimum-interest threshold / "boring day, skip" behaviour.
- No per-day price or model overrides (single config value).
- No change to `/palpite` or its generator.
