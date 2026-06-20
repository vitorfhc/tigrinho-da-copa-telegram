# Daily AI Bolãozinho — Design Spec

**Date:** 2026-06-20
**Status:** Draft for user review (Revision 2 — incorporates a 5-lens multi-agent review)
**Feature:** Daily AI-curated bolãozinho (becomes COMPLETION.md §24)

> **Naming.** As with §22/§23, the UI says **"bolãozinho"** (pt-BR) and the internal code keeps English
> identifiers (`tournament*` tables/models). This feature adds a **second, independent Gemini flow** —
> a *game-interest scorer* — that is deliberately **not** the existing `/palpite` generator (§20).

> **Revision 2 (2026-06-20) — review-driven changes.** A multi-agent review (5 lenses, adversarial
> verification) confirmed the design is sound and faithful to the codebase, with no blockers, and surfaced
> fixes now folded in: **(M1)** an atomic idempotency guarantee — a **UNIQUE** constraint on
> `auto_created_for` + `IntegrityError` handling, not just a check-then-act read; **(M2)** the Gemini
> `name` is **optional** (`= ""`) so a missing name flows to the deterministic fallback instead of
> aborting the pool; the pure `interest()`/`rank_and_select()` move to **`tigrinho/domain/daily_bolao.py`**
> and join the **coverage gate**; the migration path is corrected to **`tigrinho/db/migrations/versions/`**
> (head `d4e5f6a7b8c9`); `sanitize_name()` is defined concretely (incl. citation-tag stripping); the
> open+announce extraction covers **both** call sites (`cmd_abrir` **and** the `_do_open` callback); the
> local→UTC window is made **DST-safe**; partial Gemini coverage is handled; and success/skip
> **observability** is specified. Affected: §2–§13.

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
bolãozinho (§22) over the **top 2** (or the single game, on a one-game day). Opening reuses the exact
post-open path of a manual `/bolaozinho_abrir`: it posts to the group, @-mentions everyone, DMs all
players, and — when Splitwise is on — stamps `splitwise_mode=AUTO` (§23).

The feature is **opt-in and dormant** unless `daily_bolao_enabled` is set in `config.yaml` **and** a
Gemini key is configured — mirroring the `/palpite` gate but adding an explicit enable flag, because
auto-opening a *real-money* pot every day should never happen by accident.

**There is no heuristic fallback.** If Gemini fails or returns nothing usable, the bot creates nothing
and **DMs the admin that it failed**. The only cases that pass silently (no post, no DM) are: feature
off, zero fixtures tomorrow, or a daily bolãozinho already created for that date.

## 2. Product decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | What runs | A new daily **`run_daily` job** at `daily_bolao_time` (default **18:00**, configured timezone), scheduled in `app.py:post_init` next to the existing jobs (only when `daily_bolao_enabled`). |
| 2 | Day selected | **Tomorrow**, as a calendar day in the configured timezone (`America/Sao_Paulo`). The window is the **DST-safe** half-open interval `[tomorrow 00:00 local, day-after 00:00 local)`, each bound localized **then** converted to naive UTC to match `Game.kickoff_utc` (never `start + 24h`). |
| 3 | Candidate games | `SCHEDULED` games whose `kickoff_utc` falls in tomorrow's local-day window. Fixtures are already in the DB: the 06:00 sync pulls a 48h window, so tomorrow is present by 18:00. If the sync hasn't populated them (0 candidates), the job **skips silently** — never errors on an empty day. |
| 4 | Selection engine | **A new, independent Gemini flow** (`GameScorer` protocol + `GeminiGameScorer` client). **Not** the `/palpite` `PalpiteGenerator`. |
| 5 | Scoring | **Binary only.** Per game, Gemini returns **6 boolean criteria**; the interest score is `sum(criteria)` (0–6), **computed in pure code — the model never emits a number**. This is the only allowed "0–N score": it is *derived from multiple binary grades*. |
| 6 | How many games | **Up to 2.** Rank the scored candidates by `(interest desc, kickoff asc)`, take the top 2; on a one-game day, use that one game; **skip only when there are 0 candidates**. A 1-game pool from partial Gemini coverage is acceptable (see §5.3). |
| 7 | Activation | **Auto-open**, fully automatic (no human in the loop). Real-money, Splitwise `AUTO` when the Splitwise feature is enabled (unlinked players must link before joining — deliberate, identical to manual open; see §13). |
| 8 | Creator | `created_by = settings.admin_user_id`, so the admin can `/bolaozinho_cancelar` or otherwise manage it. |
| 9 | Entry price | **Fixed** `daily_bolao_entry_price_cents` in `config.yaml` (default `1000` = R$ 10,00), frozen at first join like any bolãozinho. |
| 10 | Name | **Gemini-proposed** (short, fun pt-BR), passed through `sanitize_name()`; deterministic fallback `Bolãozinho do dia DD/MM` whenever the sanitized name is empty. The name is **optional** in the schema — a missing name is never a hard failure (§5.3, §6). |
| 11 | Failure policy | **No selection fallback.** Gemini error / unparseable `scores` / zero usable scores on a non-empty day → **create nothing, DM admin**. |
| 12 | Idempotency | New nullable `tournaments.auto_created_for` (Date) with a **UNIQUE** constraint. The job pre-checks for an existing daily pool for the date (fast path) **and** the create commit is guarded by `IntegrityError` → rollback → skip, so concurrent fires cannot create two pots (§9). |
| 13 | Feature gate | Active iff `daily_bolao_enabled` (`config.yaml`) is `true`. Startup **fails fast** via a `@model_validator` if enabled without `GEMINI_API_KEY` or with a non-positive price. When disabled, the job is **not scheduled** at all. |

## 3. Module layout (new + touched)

```
tigrinho/ai/base.py            (+) GameScorer Protocol
tigrinho/ai/gemini.py          (+) GeminiGameScorer client (new flow, parallel to GeminiPalpiteGenerator)
tigrinho/ai/daily_bolao.py     (NEW) pydantic criteria/score models, build_scoring_prompt(), parse_scoring(),
                                     sanitize_name()  — wire format + prompt only (no coverage promise)
tigrinho/domain/daily_bolao.py (NEW) PURE: Candidate, InterestCriteria, interest(), rank_and_select()
                                     — no I/O/clock/DB; added to the coverage gate
tigrinho/daily_bolao_service.py (NEW) async orchestrator (query → score → create+open)
tigrinho/bot/daily_bolao_job.py (NEW) run_daily job + scheduler + failure DM + structured logging
tigrinho/bot/tournament_announce.py (~) shared `announce_open()` (group post + DM broadcast)
tigrinho/bot/tournament_handlers.py (~) cmd_abrir AND _do_open both call announce_open (no behaviour change)
tigrinho/bot/app.py            (~) schedule the new job (guarded by daily_bolao_enabled)
tigrinho/bot/runtime.py        (~) AppContext.game_scorer: GameScorer | None
tigrinho/__main__.py           (~) make_game_scorer(settings) factory + wiring
tigrinho/config.py             (~) daily_bolao_enabled / _time / _entry_price_cents + model_validator
tigrinho/db/models.py          (~) Tournament.auto_created_for + UniqueConstraint
tigrinho/db/repositories.py    (~) GameRepository.list_scheduled_in_window(); TournamentRepository.daily_auto_for()
tigrinho/db/migrations/versions/<rev>_add_tournament_auto_created_for.py  (NEW) append-only; down_revision d4e5f6a7b8c9
pyproject.toml                 (~) add --cov=tigrinho.domain.daily_bolao to addopts
config.example.yaml            (~) document the three new keys
COMPLETION.md / PROGRESS.md / /ajuda (~) §24 + help line
```

## 4. Data flow

```
18:00 local ── daily_bolao_job(context)
  ├─ guard: app.game_scorer is not None  (else return; defensive — job isn't scheduled when disabled)
  ├─ target_date = (datetime.now(tz) + 1d).date()
  ├─ result = await create_daily_bolao(session_factory, scorer, settings, now=utcnow(), target_date=…)
  │     ├─ start_utc, end_utc = local_day_window_utc(target_date, tz)   # DST-safe, half-open
  │     ├─ with session: if TournamentRepository(session).daily_auto_for(target_date): return SKIPPED("exists")
  │     ├─ with session: candidates = GameRepository(session).list_scheduled_in_window(start_utc, end_utc)
  │     ├─ if not candidates: return SKIPPED("no fixtures")            # 0-game day — silent
  │     ├─ system, user = build_scoring_prompt(candidates)
  │     ├─ raw = await scorer.score_games(system_instruction=system, user_content=user)   # may raise → fail
  │     ├─ batch = parse_scoring(raw)            # raises ONLY on bad `scores`; missing name is fine (= "")
  │     ├─ scores = {s.fixture_id: interest(to_domain(s.criteria)) for s in batch.scores}
  │     ├─ picks = rank_and_select(candidates, scores, limit=2)        # pure; intersect, rank, top-2
  │     ├─ if not picks: raise DailyBolaoError("Gemini não pontuou nenhum jogo válido")   # NO fallback
  │     ├─ name = sanitize_name(batch.name) or f"Bolãozinho do dia {target_date:%d/%m}"
  │     ├─ with session (create):
  │     │     t = create_tournament(session, name=name, entry_price_cents=price, created_by=admin_id)
  │     │     t.auto_created_for = target_date
  │     │     for fid in picks: add_game(session, t, fid, now=now)
  │     │     open_tournament(session, t, now=now, splitwise_enabled=settings.splitwise_enabled)
  │     │     games = TournamentRepository(session).list_games(t.id)
  │     │     mentions = _all_player_mentions(session)        # list[tuple[int, str]]
  │     │     try: session.commit()
  │     │     except IntegrityError: session.rollback(); return SKIPPED("exists")   # M1: lost the race
  │     └─ return CREATED(tournament=t, games=games, mentions=mentions)   # safe post-commit: expire_on_commit=False
  ├─ on CREATED: await announce_open(context, settings, t, games, mentions)   # shared helper
  │             log.info("daily_bolao_created", tournament_id=…, fixture_ids=picks, scores=scores)
  ├─ on SKIPPED: log.info("daily_bolao_skipped", reason=…)
  └─ except Exception as exc:                                 # one bad cycle never kills the bot (§14)
        log.error("daily_bolao_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(bot, admin_id, f"⚠️ Bolãozinho diário falhou: {exc}")
```

## 5. The new Gemini flow

### 5.1 Protocol (`tigrinho/ai/base.py`)
```python
@runtime_checkable
class GameScorer(Protocol):
    """Grades candidate games on binary interest criteria; returns raw model text (JSON)."""
    async def score_games(self, *, system_instruction: str, user_content: str) -> str: ...
```
Independent from `PalpiteGenerator` — distinct name, method, schema, AppContext field, and factory.
Tests inject a fake; production injects `GeminiGameScorer`.

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

### 5.3 Models, scoring & selection
**Wire format — `tigrinho/ai/daily_bolao.py`** (pydantic, inherits `_AiModel` → `extra="ignore"`):

| criterion | `true` when… |
|---|---|
| `decisive` | knockout, or the result decides group qualification / seeding |
| `quality_matchup` | both sides are strong / notable national teams |
| `rivalry_or_storyline` | historic rivalry or a compelling narrative |
| `star_power` | a globally famous player is likely to feature |
| `competitive_balance` | closely matched, genuinely hard to call |
| `goal_potential` | likely open / entertaining / high-scoring |

```python
class GameInterestCriteria(_AiModel):
    decisive: bool; quality_matchup: bool; rivalry_or_storyline: bool
    star_power: bool; competitive_balance: bool; goal_potential: bool

class GameInterestScore(_AiModel):
    fixture_id: int
    criteria: GameInterestCriteria

class DailyBolaoScoring(_AiModel):
    name: str = ""                 # OPTIONAL (M2): missing/null parses → flows to deterministic fallback
    scores: list[GameInterestScore]

def parse_scoring(text: str) -> DailyBolaoScoring:   # reuses extract_json(); raises only on bad `scores`
    ...

def sanitize_name(raw: str) -> str:
    """strip → drop grounding citation tags (reuse strip_citation_tags) → collapse internal whitespace/
    newlines → truncate to 60 chars → return '' if empty (caller substitutes the dated fallback).
    HTML safety is NOT done here — render uses html.escape() like every other dynamic string."""
```

**Pure domain — `tigrinho/domain/daily_bolao.py`** (no I/O/clock/DB; on the coverage gate):
```python
@dataclass(frozen=True, slots=True)
class Candidate:
    fixture_id: int
    kickoff_utc: datetime

@dataclass(frozen=True, slots=True)
class InterestCriteria:
    decisive: bool; quality_matchup: bool; rivalry_or_storyline: bool
    star_power: bool; competitive_balance: bool; goal_potential: bool

def interest(c: InterestCriteria) -> int:
    return sum((c.decisive, c.quality_matchup, c.rivalry_or_storyline,
                c.star_power, c.competitive_balance, c.goal_potential))

def rank_and_select(candidates: Sequence[Candidate], scores: Mapping[int, int], *, limit: int = 2) -> list[int]:
    by_id = {c.fixture_id: c for c in candidates}
    ranked = sorted(
        ((scores[fid], by_id[fid].kickoff_utc, fid) for fid in scores if fid in by_id),  # drop hallucinated ids
        key=lambda t: (-t[0], t[1]),                                                      # interest desc, kickoff asc
    )
    return [fid for _, _, fid in ranked[:limit]]
```
The service maps the pydantic `GameInterestCriteria` → domain `InterestCriteria` (keeps pydantic out of
`domain/`). `interest()` is the **only** numeric score and is derived purely from the six booleans.

**Partial coverage (M2/§13).** `rank_and_select` iterates the *scored* fixtures intersected with real
candidates. If Gemini scores only a subset, the pool is built from what it scored (a 1-game pool on a
3-game day is acceptable per decision #6 — "up to 2"). Only an **empty** intersection (all hallucinated /
nothing scored) returns `[]`, which the service turns into the no-fallback failure. The job **logs the
scored-vs-candidate counts** so partial coverage is observable.

### 5.4 Prompt (`build_scoring_prompt(candidates) -> tuple[str, str]`)
- **System instruction:** "You are a World Cup 2026 football analyst. For EACH fixture, grade the six
  yes/no criteria below as booleans. Do **not** output any numeric score or ranking — only the booleans.
  Also propose one short, fun Brazilian-Portuguese name for a daily betting pool over the best of these
  games. Use Google Search for current form, stakes, and lineups. Output JSON only." — followed by the
  exact criterion definitions and the JSON shape.
- **User content:** one line per candidate: `fixture_id=<id> | <home> x <away> | <kickoff_local> |
  <stage>`, where `<stage>` reuses `prompt.py`'s pt `_stage_label` (`"fase de grupos"` / `"mata-mata"`).
- `parse_scoring(text)` reuses the `extract_json` helper from `ai/schemas.py`, then validates against
  `DailyBolaoScoring`.

## 6. Service (`tigrinho/daily_bolao_service.py`)
```python
class DailyBolaoError(Exception): ...

@dataclass(frozen=True, slots=True)
class DailyBolaoResult:
    status: Literal["created", "skipped"]
    reason: str = ""                                  # "" for created; "no fixtures"/"exists" for skipped
    tournament: Tournament | None = None
    games: tuple[Game, ...] = ()
    mentions: tuple[tuple[int, str], ...] = ()        # exact shape _all_player_mentions returns

async def create_daily_bolao(
    session_factory: sessionmaker[Session],
    scorer: GameScorer,
    settings: Settings,
    *, now: datetime, target_date: date,
) -> DailyBolaoResult:
    ...
```
- **Frozen + immutable fields** (tuples, not lists) match the sibling result dataclasses.
- The Gemini call is `await`ed **outside** any DB session (network = async; SQLite = sync), matching
  `palpite_service`. Candidate reads happen in a short session; create/open in a second session.
  `expire_on_commit=False` (engine.py:41) keeps the returned ORM objects usable for the post-commit
  announce — exactly how `cmd_abrir`/`_do_open` already use these helpers.
- Returns `"skipped"` for the three silent cases (incl. the `IntegrityError` race-loss); raises
  `DailyBolaoError` (or propagates a Gemini/parse exception) for genuine failures. The **job** turns a
  raised exception into the admin DM.

## 7. Shared "open + announce" extraction
`_post_open_announcement` + `_broadcast_open_dm` are currently called from **two** sites:
`cmd_abrir` (`tournament_handlers.py:508-509`) **and** `_do_open`, the inline-keyboard "Abrir" callback
(`:915-916`). Extract the pair into `tigrinho/bot/tournament_announce.py`:
```python
async def announce_open(context, settings, tournament, games, mentions) -> None:
    await _post_open_announcement(context, settings, tournament, games, mentions)
    await _broadcast_open_dm(context, settings, tournament, games, mentions)
```
**Both** `cmd_abrir` and `_do_open` migrate onto it (their own card replies stay in place); the new job
calls the same coroutine. This guarantees an auto-opened bolãozinho is indistinguishable from a manually-
opened one — including the standard open announcement (the AI-proposed **name** + the chosen games + the
join button carry the "daily" flavour; no special template). **No behavioural change** to either path.

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
            _log.info("daily_bolao_created", tournament_id=result.tournament.id)
        else:
            _log.info("daily_bolao_skipped", reason=result.reason)
    except Exception as exc:                      # one bad cycle never kills the bot (§14)
        _log.error("daily_bolao_failed", error=str(exc), error_type=type(exc).__name__)
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
auto_created_for: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
__table_args__ = (..., UniqueConstraint("auto_created_for", name="uq_tournament_auto_created_for"))
```
- **No `index=True`** (tournaments is tiny; avoids model↔schema drift — no source column uses a bare
  `index=True`). The UNIQUE constraint is the durable idempotency guarantee. In SQLite NULLs are distinct,
  so all non-daily tournaments (NULL) coexist; only **one** row may carry a given date.
- **Append-only Alembic migration** `tigrinho/db/migrations/versions/<rev>_add_tournament_auto_created_for.py`,
  `down_revision = "d4e5f6a7b8c9"`, using `with op.batch_alter_table("tournaments") as batch_op:` to
  `add_column` **and** `create_unique_constraint("uq_tournament_auto_created_for", ["auto_created_for"])`
  (downgrade drops both). Existing rows get `NULL` — untouched.
- `TournamentRepository.daily_auto_for(target_date) -> Tournament | None`: `auto_created_for == target_date`
  (any status — DRAFT/OPEN/FINISHED/CANCELLED all block re-creation). Fast-path pre-check; the UNIQUE
  constraint + `IntegrityError` handling is the real guard.
- `GameRepository.list_scheduled_in_window(start_utc, end_utc) -> list[Game]`: `status == SCHEDULED`,
  `start_utc <= kickoff_utc < end_utc`, ordered by `kickoff_utc`.
- **DST-safe window helper** (in the service): `start_local = datetime(y,m,d, tzinfo=tz);
  end_local = start_local + timedelta(days=1); start_utc = start_local.astimezone(UTC).replace(tzinfo=None);
  end_utc = end_local.astimezone(UTC).replace(tzinfo=None)` — add the day in **local** time *before*
  converting; half-open `[start_utc, end_utc)`.

## 10. Config additions (`config.py` + `config.example.yaml`)
| field | type | default | source | notes |
|---|---|---|---|---|
| `daily_bolao_enabled` | `bool` | `False` | config.yaml | master gate; when off the job isn't scheduled |
| `daily_bolao_time` | `str` | `"18:00"` | config.yaml | local `HH:MM`; add to the existing `field_validator("sync_time","palpite_time")`; `daily_bolao_time_obj` property mirrors `palpite_time_obj` |
| `daily_bolao_entry_price_cents` | `int` | `1000` | config.yaml | R$ 10,00; must be `> 0` when enabled |

**Cross-field validation (fail fast, §3):** a `@model_validator(mode="after")` on `Settings` (fires for
both bot and CLI at load): if `daily_bolao_enabled` then require `gemini_api_key` set **and**
`daily_bolao_entry_price_cents > 0`, else raise a clear `ValueError`. `GEMINI_API_KEY` stays the only
secret (no new `.env` key). `make_game_scorer(settings)` returns `None` when `not gemini_api_key`.

## 11. Docs & maintenance (§11 rule)
- **COMPLETION.md §24** — new section: feature, decisions table, the six binary criteria (source of truth).
- **/ajuda** — one line: a daily bolãozinho opens automatically each evening over the next day's best
  games (no new command, but user-visible behaviour).
- **PROGRESS.md** — new milestone row, ticked as increments land.
- **config.example.yaml** — the three new keys with comments.

## 12. Testing plan
| layer | file | what |
|---|---|---|
| pure | `tests/test_daily_bolao_pure.py` | `interest()` truth-table; `rank_and_select` ranking, tie-break by kickoff, `limit`, anti-hallucination drop, **partial coverage (3 candidates, 1 scored → `[that one]`)**, empty intersection → `[]`. **On the coverage gate — ~100% line+branch.** |
| AI parse | `tests/test_daily_bolao_ai.py` | `parse_scoring` happy path, fenced JSON, prose-wrapped, **name omitted → `name == ""`**, bad `scores` → raises; `sanitize_name` strips citation tags / collapses whitespace / truncates / empty→`""`. |
| client | `tests/test_gemini_generator.py` | `GeminiGameScorer` via the existing `genai.Client` monkeypatch; asserts config (grounding + thinking) and empty-text → raises. |
| service | `tests/test_daily_bolao_service.py` | `FakeScorer` (+ `.calls` counter) + in-memory DB: **created** → right games/price/name, `reason==""`, `calls==1`, AUTO when `splitwise_enabled`; **skips** (idempotency, 0-fixture) → `status=="skipped"`, right `reason`, **no row for the date**, **`calls==0`**; **no-fallback** (scorer raises / empty / bad JSON / hallucinated-only) → raises, **nothing created**; **valid scores + name omitted** → creates with dated default name; **IntegrityError race** → `skipped("exists")`. |
| job | `tests/test_daily_bolao_job.py` | no-op when scorer `None`; created path calls `announce_open` + logs `daily_bolao_created`; skip path logs `daily_bolao_skipped`; exception → `notify_admin` + `daily_bolao_failed`, no crash. |
| shared | `tests/test_tournament_handlers.py` | **both** `cmd_abrir` and `_do_open` still post group + DM via `announce_open` after the extraction (no regression — extends the existing `test_open_callback_dms_known_players`). |
| config | `tests/test_config.py` | new fields + `daily_bolao_time_obj`; `@model_validator`: (a) enabled+no key → `ValidationError`, (b) enabled+key+`price=0` → `ValidationError`, (c) enabled+key+valid → OK; add `daily_bolao_*` to the env clear-list so host env can't leak. |
| migration | `tests/test_migrations.py` | upgrade adds `auto_created_for` + the UNIQUE constraint; existing rows `NULL`; a second row with the same date violates the constraint. |

`FakeScorer` mirrors `FakeGenerator`: a `.calls` counter + canned JSON for `score_games`, configurable per
fixture criteria + name. All four gates green: `ruff check`, `ruff format --check`, `mypy --strict`,
`pytest`. The pure `domain/daily_bolao.py` stays I/O-free and joins `--cov-fail-under=100`.

## 13. Edge cases
- **0 fixtures tomorrow** → silent skip (no post, no DM) — also the graceful outcome if the 06:00 sync hasn't run.
- **1 fixture** → single-game bolãozinho (still scored + named via Gemini).
- **Already created for the date** (rerun / redeploy / two pollers at 18:00) → skip via pre-check **and** the
  UNIQUE constraint + `IntegrityError` (durable across processes; a lock alone would not be).
- **Gemini scores only hallucinated ids** (none match candidates) → `picks == []` → raise → admin DM.
- **Gemini scores a subset of candidates** → pool built from the scored subset (≤2); logged.
- **Blank / citation-only name** → `sanitize_name` returns `""` → `Bolãozinho do dia DD/MM`.
- **Unlinked players + Splitwise on** → the AUTO join-guard means they must `/vincular_splitwise` before
  `/entrar`. **Intended** and identical to a manual AUTO open; `nudge-splitwise` / `register-splitwise
  --force` (§23) mitigate onboarding. Splitwise **off** → opens MANUAL like any bolãozinho.

## 14. Out of scope (YAGNI)
- No new command, keyboard wizard, or admin-review step (activation is fully automatic per the chosen
  design). The admin manages the result via existing `/bolaozinho_*` commands.
- No minimum-interest threshold / "boring day, skip" behaviour.
- No per-day price or model overrides (single config value).
- No change to `/palpite` or its generator.
- No special daily-pool announcement template — the standard open announcement carries the AI name + games.
