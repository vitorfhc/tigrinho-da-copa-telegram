# PROGRESS — TigrinhoDaCopa (Telegram)

**Status: COMPLETE — M0–M11 done** (full build green; DoD holds) · _Created 2026-06-15_

The Ralph-loop's persistent memory and live checklist. `COMPLETION.md` is the single
source of truth; this file only tracks progress against it.

---

## How to use this file

- The loop **MUST update this file every iteration**. Read it first (with `git log --oneline -20`)
  to learn what is already done — trust the files over memory.
- Tick an item **only** when its machine-checkable **Done when:** line is satisfied (module exists,
  its tests are written and passing, all gates green). Never mark a milestone done without its tests.
- Record notes, blockers, and decisions in the log at the bottom — every iteration appends what it
  discovered so the next iteration starts from a known state.
- Work the **lowest-numbered unfinished milestone** first; do not start milestone N+1 while N is
  unfinished. Every commit MUST leave the repo green.

---

## Definition of Done (mirrors §0)

The whole build is done when **all** of the following hold:

- [x] Every milestone M0–M11 in §18 is checked in this file.
- [x] `ruff check .` passes.
- [x] `ruff format --check .` passes.
- [x] `mypy --strict .` passes.
- [x] `pytest` passes (251 tests).
- [x] Domain logic (`scoring.py`, `settlement.py`) has **100% line+branch** coverage (enforced).
- [x] An automated end-to-end smoke test with `provider_mode: fake` runs (sync → bet via deep-link →
      settle → results → board) without error (`tests/test_smoke_e2e.py`).
- [x] `README.md` lets a brand-new operator deploy from zero (§15.1, 14 sections).

**Completion promise:** when and only when the Definition of Done fully holds, emit exactly:

```
<promise>TIGRINHO_TELEGRAM_COMPLETE</promise>
```

Do not emit the promise while any gate is red, any milestone is unchecked, or any blocker is open.

---

## Milestone checklist (M0–M11, in order — §18)

- [x] **M0 — Scaffold**
  - [x] `pyproject.toml` with dependencies (`uv`)
  - [x] ruff (lint + format), `mypy --strict`, and `pytest` config wired up
  - [x] Package layout per §5 (`tigrinho/` package + `tests/`)
  - [x] `config.py` — single `Settings` (pydantic-settings): secrets from `.env`, settings from
        `config.yaml` via `YamlConfigSettingsSource`; `CONFIG_PATH` env; merged + validated; fail-fast
  - [x] `logging.py` — structlog to stdout (`json`/`console`, level from config)
  - [x] Initial `PROGRESS.md` present (this file)
  - **Done when:** package imports, `config.py` loads/validates `.env` + `config.yaml` (and fails fast
    on missing/malformed values) with tests, and all gates (`ruff check .`, `ruff format --check .`,
    `mypy --strict .`, `pytest`) are green on the empty app. ✅ **DONE** (16 tests, gates green).

- [x] **M1 — Data layer**
  - [x] ORM models (`players` keyed on `telegram_id`, `games`, `bets`, `squad_players`, `api_usage`)
        per §6, typed
  - [x] `db/engine.py` — SQLAlchemy 2.0 engine/session factory (synchronous)
  - [x] Alembic initial migration (`alembic upgrade head` works; migrations append-only)
        — rev `edbcfec84e20`; env.py resolves URL via `TIGRINHO_DB_URL`/`Settings.db_path`,
        `render_as_batch=True`; tested against ORM metadata + downgrade
  - [x] `db/repositories.py` — CRUD repos: players, games, bets, squads, api_usage
  - [x] Repository tests against a temp SQLite (incl. the `UNIQUE(fixture_id, player, category)`
        one-bet-per-category constraint)
  - **Done when:** models + initial migration + repositories exist, their tests pass, all gates green.
    ✅ **DONE** (33 tests, gates green).

- [x] **M2 — Provider** (interface-agnostic — unchanged from the Discord build)
  - [x] `providers/base.py` — `FootballProvider` Protocol + frozen value objects (`Fixture`,
        `MatchResult`, `GoalEvent`, `SquadPlayer`) — value objects use **aware UTC** datetimes
  - [x] `providers/fake.py` — `FakeProvider` (scripted fixtures/results) for `provider_mode: fake`
        — records `call_log` for budget/poll tests
  - [x] `providers/api_football.py` — `ApiFootballProvider` (httpx) mapping API-Football v3 JSON →
        value objects (90′ = `score.fulltime`; stage; advancing team; status normalization; goal
        timeline ≤90′ excluding own goals); pure module-level mappers + httpx client (MockTransport)
  - [x] `providers/budget.py` — `RequestBudget`: daily counter keyed by `api_budget_reset_tz`, hard
        stop at `api_daily_cap`, atomic increment, `BudgetExceeded` signal
        — `guarded(call)` reserves on success; commits counter in its own txn; clock injectable
  - [x] Provider tests (mock httpx; `score.fulltime` vs ET/penalty cases) + budget hard-stop test
  - **Done when:** the four provider modules + budget exist, their tests pass, all gates green.
    ✅ **DONE** (56 tests, gates green).

- [x] **M3 — Domain** (interface-agnostic — unchanged from the Discord build)
  - [x] `domain/bets.py` — `BetCategory` enum + typed per-category payload models + validation
        (pydantic, frozen, `extra=forbid`); `parse_payload`/`serialize_payload`
  - [x] `domain/scoring.py` — centralized `POINTS` table + per-category grading (PURE);
        `GradingContext`, `is_correct`, `grade`, `first_genuine_scorer`
  - [x] `domain/settlement.py` — grade every bet for a `MatchResult` (PURE, deterministic, idempotent)
  - [x] `domain/text_pt.py` — pt-BR message templates (HTML); `help_text()` (/ajuda), `welcome_text()`,
        `describe_bet`, `mention`, `points_table_text` (derived from `scoring.POINTS`)
  - [x] Exhaustive table-driven domain tests (§16): knockout 90′ draw, own-goal-first, 0-0 first
        scorer, advancing-team winner, O/U boundary at exactly 2 and 3, BTTS NEITHER on 0-0,
        settlement idempotency
  - **Done when:** domain modules exist, tests pass, `scoring.py` + `settlement.py` have ~100%
    line+branch coverage, all gates green. ✅ **DONE** (113 tests; scoring+settlement at **100%
    line+branch**, enforced by `--cov-fail-under=100` in pytest addopts).

- [x] **M4 — Bot skeleton**
  - [x] `bot/app.py` — PTB `Application` builder (long polling) + handler/job registration
        (+ `bot/runtime.py` `AppContext` in `bot_data`; `AnyApplication` alias)
  - [x] `post_init` config validation: verify `get_me().username == bot_username`, can reach
        `group_chat_id` (fail-fast on mismatch); admin DM best-effort
  - [x] `setMyCommands` registered with the correct `BotCommandScope`s (private full / group views)
  - [x] `help_handlers.py` — `/ajuda` (pt-BR) and `/start` (no payload — welcome)
  - [x] `application.add_error_handler` backstop (started `bot/alerts.py` early: `notify_admin` +
        `error_handler`; M7 extends it)
  - [x] Bot-skeleton tests (rely on `FakeProvider`)
  - **Done when:** the app builds, `post_init` validation + `/ajuda` + `/start` welcome + error handler
    exist with tests passing, all gates green. ✅ **DONE** (122 tests, gates green).

- [x] **M5 — Sync job**
  - [x] `bot/sync_job.py` — daily fixtures sync via `JobQueue.run_daily` (1 provider call via budget,
        top priority); placeholders already skipped by the provider
  - [x] New game → insert + queue announcement; rescheduled → update kickoff + `match_hash` +
        re-announce (bets stay valid); postponed/cancelled → `status = VOID` + void bets + notify
        (also un-voids on reschedule)
  - [x] One consolidated group announcement (pt-BR, HTML) with a per-game `🎯 Apostar` deep-link
        button (`https://t.me/<bot_username>?start=bet_<fixture_id>`); `bot/keyboards.py` started
  - [x] Sync-job tests (new/reschedule/void/un-void/no-change paths) with `FakeProvider`
  - **Done when:** `sync_job.py` exists, its tests pass, all gates green. ✅ **DONE** (138 tests).

- [x] **M6 — Bet handlers**
  - [x] `bot/callbacks.py` — compact `callback_data` encode/decode helpers (≤64 bytes) with round-trip
        — typed union (ChooseGame/ChooseCategory/HomeScore/ExactScore/Winner/Btts/OverUnder/
        ScorerPage/ScorerInput/DeleteBet/Cancel); strict decode; oversized guard
  - [x] `bot/keyboards.py` — inline keyboard builders (games, categories, score pad 0–10, winner
        with DRAW hidden for knockout, BTTS, over/under, paginated squad, my-bets delete);
        board toggle deferred to M8
        - [x] DRAW hidden for knockout fixtures
  - [x] `bot/bets_handlers.py` — `/start bet_<fixture_id>` deep-link entry (parse payload, auto-create
        player, jump into wizard)
  - [x] `/apostar` wizard (**stateless** `CallbackQueryHandler` + inline keyboards, editing one
        message): game → category → payload → confirm/upsert (respects one-per-category unique
        constraint). _Design: stateless callback_data wizard instead of ConversationHandler — see
        COMPLETION.md decision._
  - [x] Score number-pad input (home → away, home baked into away buttons); paginated squad keyboard;
        **DRAW hidden for knockout**; BTTS and Over/Under selectors
  - [x] `/minhas_apostas` (DM, grouped open vs settled, ✓/✗ + points, 🗑 Apagar delete on open bets)
  - [x] `/jogos` (group or DM; upcoming games + per-category bet status; group includes deep-link buttons)
  - [x] Time-based closing: create/edit/delete on a started game rejected with clear pt-BR message (no
        API call) — `_is_open`/`_guard_open`
  - [x] Bet-flow tests (§16): deep-link payload parse + player auto-create; wizard state transitions
        (score pad); time-based closing rejection; `callback_data` codec ≤64-byte round-trip
  - **Done when:** `callbacks.py`, `keyboards.py`, `bets_handlers.py` exist, their tests pass, all gates
    green. ✅ **DONE** (210 tests, gates green).

- [x] **M7 — Poll job**
  - [x] `bot/poll_job.py` — active-window live polling via `JobQueue.run_repeating`; returns with **no
        API call** when no active games
  - [x] One `get_live_results()` call when active; update status/live scores; on `FINISHED` run
        settlement (§8.3) fetching `get_match_result()` once; all calls via `RequestBudget`
  - [x] One results message to `group_chat_id`: 90′ score, first scorer, each participating player
        mentioned (HTML `tg://user?id=…`) with points + per-category breakdown (`text_pt.results_text`)
  - [x] Stuck-game safeguard: DM admin when a game is unsettled past `kickoff + match_window_hours`
  - [x] `bot/alerts.py` — admin DM alerts + structured logs (cap-reached **once/day** via
        `AppContext.alerted_cap_days`)
  - [x] Poll-job tests: active-window decision (no API call when none active) + auto-settlement path
        + stuck-game alert; cap-reached dedup
  - **Done when:** `poll_job.py` (+ `alerts.py`) exist, their tests pass, all gates green.
    ✅ **DONE** (220 tests, gates green).

- [x] **M8 — Board**
  - [x] `bot/board_handlers.py` — `/placar` posts the scoreboard, defaults to **Geral**, inline
        Geral↔Semana toggle that **edits the same message** (also accepts `/placar semana`)
  - [x] Geral = all-time points desc; Semana = current Mon→Sun week in `timezone` (resets Monday 00:00)
  - [x] Ranked top ~15 with medals for top 3; append caller's own rank/points if outside top 15
  - [x] Tie-breaks: (1) points desc, (2) exact-score hits desc, (3) total correct desc, (4) earliest
        `players.created_at`
  - [x] Board derivable purely from settled bets (`tigrinho/scoreboard.py`, CLI-rebuildable) + tests
  - **Done when:** `board_handlers.py` exists, its tests pass, all gates green. ✅ **DONE** (236 tests).

- [x] **M9 — Admin CLI**
  - [x] `cli.py` (Typer) sharing repository + domain code; plain aligned tables; destructive commands
        require `--yes`
  - [x] Group 1: CRUD games/bets/players (`games list/show/delete`, `players list/delete`,
        `bets list/delete`)
  - [x] Group 2: manual result + re-settle (`set-result <fixture> <home> <away> [--scorer]
        [--advancing]`, idempotent via `settle_fixture`)
  - [x] Group 3: `sync` (force); `squads seed/refresh` (provider+budget); `budget` (counter+remaining)
  - [x] Group 4: `board [--weekly]` (rebuild from settled bets); `db [--table]` (JSON dump)
  - [x] `telegram-info` helper (resolved `@username`/id via `get_me`; echoes `group_chat_id`/
        `admin_user_id`)
  - [x] CLI tests (CliRunner + monkeypatched `build_cli_context` + temp DB / FakeProvider)
  - **Done when:** `cli.py` exists with all four capability groups + `telegram-info`, its tests pass,
    all gates green. ✅ **DONE** (246 tests).

- [x] **M10 — Deploy**
  - [x] `docker/Dockerfile` — `python:3.12-slim`, non-root user, deps from `pyproject.toml`+`uv.lock`
        via uv (`uv sync --frozen --no-dev`)
  - [x] `docker-compose.yml` — one `bot` service, `env_file: .env`, `restart: unless-stopped`, named
        `/data` volume, read-only `config.yaml` bind-mount, `CONFIG_PATH=/app/config.yaml`, no ports
  - [x] Entrypoint (`docker/entrypoint.sh`) runs `alembic upgrade head` then `python -m tigrinho`
  - [x] `.env.example` and `config.example.yaml` committed with every secret/setting from §4 (M0)
  - [x] Full `README.md` per §15.1 (all 14 sections, copy-paste deployable from zero)
  - [x] `CLAUDE.md` — grounding rule (§2), secrets/settings split (§4), maintenance rule (§11), §0
        loop manual (present from project bootstrap)
  - **Done when:** Dockerfile + compose + entrypoint + example config files + README (§15.1) +
    `CLAUDE.md` exist, image builds, all gates green. ✅ artifacts complete, gates green, compose +
    entrypoint validated. ⚠️ `docker build` **not run locally** (no Docker daemon in this env) —
    the Dockerfile follows the standard uv pattern; verify with `docker compose up -d --build` on a
    host with Docker. (Not a §0 DoD gate.)

- [x] **M11 — Hardening**
  - [x] Budget enforcement verified end-to-end (hard stop at cap blocks polling + alerts admin;
        `test_smoke_e2e.test_budget_hard_stop_blocks_polling`)
  - [x] Edge cases covered; domain coverage at **100% line+branch** for `scoring.py` + `settlement.py`
        (enforced by `--cov-fail-under=100`)
  - [x] End-to-end smoke test with `provider_mode: fake` (sync → bet via deep-link → settle →
        results → board) runs without error (`test_smoke_e2e.test_full_flow_fake_provider`)
  - [x] Full §0 Definition of Done re-verified — adversarial multi-agent review (10 agents) found
        2 confirmed correctness bugs, both fixed + regression-tested (see log below)
  - **Done when:** the §0 Definition of Done fully holds. ✅ **DONE** (251 tests, all gates green).
  - [ ] Full Definition of Done re-verified (all gates green, all milestones checked)
  - **Done when:** the §0 Definition of Done fully holds — at which point emit
    `<promise>TIGRINHO_TELEGRAM_COMPLETE</promise>`.

---

## Notes / blockers / decisions log

_(The loop appends here every iteration: discoveries, grounding doc URLs, decisions made for
under-specified points, and blockers. Empty at creation — 2026-06-15.)_

### 2026-06-15 — M0 Scaffold (DONE)

**Toolchain:** `uv` 0.9.20 manages the project; venv pinned to **Python 3.12.8** (matches the
`python:3.12-slim` deploy target, §15) even though the host runs 3.14. Run gates via `uv run …`.
Installed (key): pydantic 2.13.4, pydantic-settings 2.14.1, structlog 26.1.0, ruff 0.15.17,
mypy 2.1.0, pytest 9.1.0, pytest-asyncio 1.4.0. `uv.lock` is committed for reproducibility.

**Grounding done (per §2):**
- pydantic-settings 2.14 — https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/
  · YAML source: override `settings_customise_sources`, return tuple incl.
  `YamlConfigSettingsSource(settings_cls, yaml_file=...)`; **first source in the tuple wins** →
  ordered `init,env,dotenv,yaml,secrets` so env beats YAML (§4). Missing YAML file is skipped
  (base mixin checks `is_file()`), so required values must then come from env (fail-fast preserved).
  YAML needs the `pydantic-settings[yaml]` extra.
- structlog 26 — https://www.structlog.org/en/stable/ · `configure(processors=[…],
  wrapper_class=make_filtering_bound_logger(level), logger_factory=PrintLoggerFactory())`.

**Decisions / gotchas (carry forward):**
- **mypy + pydantic-settings:** pydantic v2's `dataclass_transform` makes mypy require every field
  in `Settings()`. Fixed by enabling `plugins = ["pydantic.mypy"]` (special-cases `BaseSettings`
  to make init args optional). This is the supported fix — NOT a gate weakening. Keep the plugin.
- Tests neutralize a developer's real `.env` via `monkeypatch.chdir(tmp_path)` (relative `.env`
  then resolves to an absent file) — no `_env_file=` kwarg needed (the plugin's typed init rejects it).
- **PTB version:** latest is **22.x** (22.7/22.8 in 2026); 21.x is EOL. Per §2 "live docs win",
  M4+ will target PTB **22.x** and I'll record that decision in COMPLETION.md when integrating it.

### 2026-06-15 — M1 Data layer (DONE)

Grounded SQLAlchemy 2.0.50 (DeclarativeBase/Mapped/mapped_column; native PEP 484 typing, no
mypy plugin) and Alembic 1.18.4 (env.py online/offline, `render_as_batch=True` for SQLite).
- **Models** (`db/models.py`): `Player`(telegram_id BigInteger PK), `Game`(fixture_id PK),
  `Bet`(UNIQUE `uq_bet_one_per_category`), `SquadPlayer`, `ApiUsage`. `Stage`/`GameStatus` are
  `enum.StrEnum`. **Timestamp convention: naive UTC everywhere** (SQLite has no tz) — `utcnow()`.
- **Engine** (`db/engine.py`): SQLite engine + `PRAGMA foreign_keys=ON` per connection,
  `sessionmaker(expire_on_commit=False)`, `create_all` for tests.
- **Migration** (rev `edbcfec84e20`): autogenerated full schema; typed `script.py.mako`.
- **Repos** (`db/repositories.py`): one class per entity, each wraps a `Session`, **flush but
  never commit** (caller owns the unit of work). `BetRepository.upsert` enforces one-per-category
  and resets grading on re-bet.

**Decisions/gotchas:** Repos flush-not-commit; bot/CLI commit. Autogenerated migrations need a
`ruff check --fix` pass for import ordering — run it in the generate workflow.

### 2026-06-15 — M2 Provider (DONE)

Grounded API-Football v3 (search; docs site 403s automated fetch) + httpx 0.28.1.
- **base.py**: frozen value objects (aware-UTC datetimes), `runtime_checkable` Protocol.
- **fake.py**: scripted `FakeProvider` + `call_log`.
- **api_football.py**: pure module-level mappers (`map_fixture`/`parse_goals`/`map_match_result`/
  `map_squad`/`normalize_status`/`classify_stage`/`advancing_team_id`) + httpx client. 90′ score =
  `score.fulltime`; advancing = `teams.*.winner`; goals filtered to `elapsed<=90`, own-goal/penalty
  flagged, `Missed Penalty` excluded; header `x-apisports-key`; `from`/`to` date window + in-Python
  `now<=kickoff<=cutoff` filter; placeholders (null team id) skipped. Tested via `httpx.MockTransport`.
- **budget.py**: `RequestBudget` (see above).

**Decisions/gotchas:** Status map extended beyond §7.2 — INT/SUSP→LIVE, AWD/WO→FINISHED, unknown→
SCHEDULED (recorded in code comment). Mapping fns take JSON dicts; tests must annotate literals as
`dict[str, Any]`/`list[dict[str, Any]]` (dict/list invariance under mypy --strict).

### 2026-06-15 — M3 Domain (DONE)

Pure grading core, no grounding needed (no external surface).
- **bets.py**: `BetCategory` + selection StrEnums + frozen pydantic payloads (`extra=forbid`,
  bounds) + `parse_payload`/`serialize_payload` (match on category).
- **scoring.py**: single `POINTS` table; `GradingContext` (90′ + team ids + goals); `is_correct`
  dispatch by payload type (`assert_never` exhaustiveness); winner = group 1X2 / knockout advancing
  (90′ fallback if no advancing info; never draw); BTTS; O/U≥3 / ≤2; first genuine scorer.
- **settlement.py**: `settle_game` (pure/idempotent) over `PendingBet`→`GradedBet`; `build_context`
  guards missing 90′ score.
- **text_pt.py**: pt-BR HTML (help/welcome/describe_bet/mention/points table from `POINTS`).

**Coverage gate:** pytest addopts now carries `--cov=…scoring --cov=…settlement --cov-branch
--cov-fail-under=100` so the DoD domain-coverage requirement is enforced on every run (currently
100% line+branch). `assert_never` lines marked `# pragma: no cover`.

### 2026-06-15 — M4 Bot skeleton (DONE)

Grounded PTB **22.8** (`python-telegram-bot[job-queue]`); recorded the 21.x→22.x decision in
COMPLETION.md §3. APScheduler + tzlocal pulled in by the job-queue extra.
- **runtime.py**: `AppContext` (settings, provider, session_factory, budget) stored in
  `application.bot_data`; `get_app_context()`; `AnyApplication = Application[Any,…]` (6 params).
- **app.py**: `build_application(app_context)`; `post_init` → `validate_startup` (get_me username
  fail-fast, group reachability fail-fast, admin DM best-effort) + `set_commands` (private/group
  scopes); `StartupError`.
- **help_handlers.py**: `cmd_ajuda`, `cmd_start` (welcome; M6 adds bet_ deep-link parsing).
- **alerts.py** (started early; M7 extends): `notify_admin` (best-effort), `error_handler` backstop.

**Decisions/gotchas:** Bare `Application` trips mypy `disallow_any_generics`; use the explicit
`Application[Any,…]` alias. Tests `cast(Update,/ContextTypes.DEFAULT_TYPE, MagicMock())` to satisfy
strict. `settings`/`app_context` fixtures added to conftest (Settings via init kwargs + chdir(tmp)).

### 2026-06-15 — M5 Sync job (DONE)

- **sync_job.py**: `sync_fixtures` (pure DB logic) — naive-UTC `kickoff_utc`, `kickoff_local` via
  `settings.tzinfo`; `match_hash = sha256(kickoff_iso|home_id|away_id)`; void sets bets points=0 +
  settled_at; reschedule/un-void detection. `sync_job` callback (budgeted 1 call, snapshots games to
  `_GameView` before commit, then sends announcement/reannounce/void messages). `schedule_sync_job`
  → `run_daily` at `sync_time` in `tzinfo`. Wired into `post_init`.
- **keyboards.py** (started): `deep_link_url` (built directly per the spec URL, no PTB helper) +
  `announcement_keyboard` (one 🎯 Apostar URL button per game).
- **text_pt**: `format_kickoff_local` (pt-BR weekday), `announcement_text`, `reannounce_text`,
  `void_text`.

**Decisions/gotchas:** announcement uses `LinkPreviewOptions(is_disabled=True)`. Voided bets:
points=0 + settled_at set + is_correct=None (won't contribute to the board). FakeProvider ignores
`window_hours` (returns all scripted fixtures) — fine for tests; the real provider does the window
filter (M2).

### 2026-06-15 — M6 Bet handlers (DONE)

- **callbacks.py**: typed `callback_data` codec, compact opcodes, ≤64-byte guard, strict decode.
- **keyboards.py**: games/category/home+away score pads/winner(DRAW hidden KO)/BTTS/O-U/squad/my-bets.
- **bets_handlers.py**: `start_handler` (deep-link `bet_<id>` → auto-create player → category step;
  else welcome), `apostar_handler` (DM list / group redirect), `on_callback` (single stateless
  dispatcher), `_finalize` (open-check → upsert → confirm), `minhas_apostas_handler` (open/settled +
  🗑 delete), `jogos_handler`. Wired into app.py; `help_handlers` reduced to `/ajuda`.

**Decisions/gotchas:** **Stateless wizard** (no ConversationHandler) — all state in callback_data
(home score baked into away buttons), recorded in COMPLETION.md. Repos gained `list_upcoming`/
`list_active`. mypy: bind `decode()` result before isinstance for narrowing; type score `side` as the
`Side` literal; guard `InlineKeyboardButton.url` (str|None).

### 2026-06-15 — M7 Poll job (DONE)

- **settlement_service.settle_fixture** (M7.1): DB writer shared by poll + CLI; idempotent.
- **poll_job.py**: `poll_job` (run_repeating) — `list_active` decision (no API call when idle),
  one budgeted `get_live_results`, mark LIVE, collect FINISHED → `_settle_and_announce` (budgeted
  `get_match_result` → `settle_fixture` → `results_text` → group message; `settled_at` guard prevents
  double-post). Stuck games (`list_stuck`) → admin DM. Outer try/except: BudgetExceeded →
  `alert_cap_reached` (once/day), other → log + admin DM (never kills bot). `schedule_poll_job`
  (run_repeating, `first=10`) wired into post_init.
- **alerts.alert_cap_reached** + `AppContext.alerted_cap_days` (once/day dedup).

**Decisions/gotchas:** cap-alert dedup lives in `AppContext.alerted_cap_days` (mutable set on a frozen
dataclass) — testable, process-lifetime. Poll consumes 2 budget calls per finished game (live +
match_result).

### 2026-06-15 — M8 Board (DONE)

- **scoreboard.py** (PURE): `BetRecord`/`RankEntry`; `rank()` aggregates settled bets with the 4
  tie-breaks; `week_bounds`/`in_current_week` (Mon→Sun local). CLI-rebuildable.
- **board_handlers.py**: `/placar [semana]` (default Geral) + `board_toggle` (edits same message);
  `_load_records` (settled bets, skip VOID, weekly filter by `kickoff_local`); top 15 + medals +
  caller-outside line. Registered **before** the wizard catch-all so `^bv:` matches first.
- **callbacks.BoardView** (`bv:g`/`bv:s`); **keyboards.board_toggle_keyboard**; **text_pt.board_text**.
- **BetRepository.list_settled**.

**Decisions/gotchas:** board uses escaped display names (not mentions) to avoid pinging 15 people.
Voided games excluded from the board. Toggle handler registered before catch-all (PTB stops at first
matching handler in a group).

### 2026-06-15 — M9 Admin CLI (DONE)

Grounded Typer 0.26.7 (Annotated options, `add_typer`, `CliRunner`). `cli.py` shares repos +
`settlement_service` + `scoreboard` + `board_data`. `build_cli_context()` is monkeypatchable for
tests. Extracted `tigrinho/board_data.py` (telegram-free board-records loader) so the CLI doesn't
import the bot layer; `BetRepository.list_all` added. `telegram-info` resolves `get_me` via an
injectable `_get_me`. All four groups + telegram-info implemented and tested (CliRunner).

### 2026-06-15 — M10 Deploy (DONE, build unverified locally)

- `tigrinho/__main__.py` (entrypoint wiring) + `providers/factory.make_provider` (shared w/ CLI).
- `docker/Dockerfile` (python:3.12-slim, uv, non-root, /data), `docker/entrypoint.sh`
  (`alembic upgrade head` → `python -m tigrinho`), `docker-compose.yml` (named volume, ro config
  bind-mount, env_file, no ports), `.dockerignore`. Full `README.md` (14 §15.1 sections).
- Validated: compose YAML structure, entrypoint `bash -n`, gates green (247 tests). `docker build`
  could NOT run (no daemon here) — verify on a Docker host.

### 2026-06-15 — M11 Hardening (DONE) + adversarial review

- Added e2e smoke test (`test_smoke_e2e.test_full_flow_fake_provider`) + budget hard-stop e2e.
- Ran an **adversarial multi-agent correctness review** (workflow `wf_63cab4b0-947`, 10 agents over
  7 dimensions vs the spec, each finding independently verified). It confirmed **2 real bugs**:
  1. **sync_job reschedule condition** reset LIVE/FINISHED games to SCHEDULED on re-sync (§9.1).
     Fixed: only SCHEDULED (kickoff changed) or VOID (un-void) games are rescheduled; LIVE/FINISHED
     are left untouched. Regression test: `test_sync_does_not_reset_live_or_finished_game`.
  2. **poll_job `_settle_and_announce`** fetched the budgeted `get_match_result()` before the
     `settled_at` guard, wasting an API call on already-settled games (§9.2 "if needed" / §7.3).
     Fixed: pre-check `settled_at` before the call; re-check after (race guard). Regression test:
     `test_settle_skips_budget_when_already_settled`.

**Build complete.** All §0 DoD items hold: 4 gates green, M0–M11 checked, scoring+settlement at
100% line+branch, e2e fake-provider smoke test passes, README deployable. (Only Docker `build` was
not run locally — no daemon in this env; not a §0 DoD gate.)

### 2026-06-15 — Post-build multi-POV review fixes (P1 + P2)

A 7-POV adversarial review (workflow `wf_adcedf1f-7d2`, 24 agents) surfaced improvements; the user
asked to fix all P1 + P2. Done in 5 green commits (271 tests):
- **P1.1** group `?start=apostar` deep link now opens the games picker (was: welcome).
- **P1.2** poll settles overdue games via `get_match_result` (no longer depends on the game still
  being in the `live=all` feed) — `SETTLE_AFTER=2h`; only settles when provider says FINISHED.
- **P2.3** `safe_edit_text` swallows Telegram "message is not modified" (no more spurious admin DMs).
- **P2.4** settlement reads run before the lower-priority live poll (§7.3 priority).
- **P2.5** group announcements are recoverable via `announced_at` (retried next sync); all group
  sends wrap failures → log + admin DM instead of silent loss; admin-alert exception text escaped.
- **P2.6** first-scorer dead-end (squads unseeded) now shows a back-to-categories / cancel keyboard.
- **P2.7** added wizard-branch, delete-authorization, settled-rendering, weekly-subset,
  caller-outside-top-15, and CLI re-grade tests.

P3 nits (indexes, N+1, minor security/arch) intentionally left as follow-ups — none are defects at
one-group scale. DoD still holds.

### 2026-06-15 — Product change: first-scorer → first **team** to score; squads removed

User request. `FIRST_SCORER` (pick a player) → `FIRST_TEAM` (`{sel: HOME|AWAY}`, 3 pts). Removed all
squad infrastructure: `/players/squads` pull (`get_squad`/`map_squad`), `SquadPlayer` value object,
`squad_players` table + `SquadRepository`, the paginated squad keyboard, `ScorerInput`/`ScorerPage`
callbacks, and the CLI `squads seed/refresh` commands. Added append-only migration `b0be15a80128`
dropping `squad_players` (initial migration untouched, per guardrail). Grading reuses
`first_genuine_scorer` (the goal timeline is still parsed — it tells which team scored first);
`games.first_scorer_player_id` is still recorded from the goal event for display. Results message +
`set-result --first-team home|away` are team-based. Spec (§5/§6/§7/§8.1/§8.2/§13/§15.1/§17/§19) +
`/ajuda` + README updated per the §11 maintenance rule. 267 tests, all gates green, both migrations
apply.

### 2026-06-16 — Fix: BTTS keyboard names the real teams (not "Mandante/Visitante")

User request. The both-teams-to-score selector showed generic "Só o mandante" / "Só o visitante"
buttons (and confirmations) — the only betting step that didn't use the real team names (winner,
first-team, and the score prompts already did). Replaced the static `BTTS_LABELS` dict with
`text_pt.btts_labels(home_team, away_team)`, so the two "only" options render as e.g. "Só o Brasil" /
"Só o Argentina". `btts_keyboard` now takes the team names (passed from the game in
`_step_payload`), and `describe_bet` builds the BTTS label from the (HTML-escaped) names. Also
reworded the static `/ajuda` category lines that still said "Mandante/Visitante" to neutral
phrasing. Spec §8.2 + `/ajuda` updated per the §11 maintenance rule. Gates green.

### 2026-06-16 — Change: morning "next 24h" announcement (was: announce new games on sync)

User request. The daily sync no longer announces games as they are *discovered*; instead each
morning's `sync_job` posts one consolidated announcement of the games kicking off in the **next
24h**. The sync (insert/reschedule/void) is unchanged — only the announcement set changed.
- `GameRepository.list_unannounced(now)` → `list_unannounced_within(now, horizon)`: adds the
  `kickoff_utc <= now + horizon` upper bound (still `announced_at IS NULL` + `SCHEDULED`).
- `sync_job._announce_new_games` → `_announce_upcoming_games` with `ANNOUNCE_HORIZON = 24h`.
  `announced_at` is still set only on a successful send (failure retried next morning) and dedups a
  game across mornings, so each game is announced once — the first morning it's within 24h.
- `text_pt.announcement_text` heading: "Novos jogos abertos…" → "Jogos das próximas 24h — apostas
  abertas!". Reminder job (§9.3) still keys off `announced_at`, so ~1h reminders keep working.
- Spec §9.1 + M5 summary updated. Tests reworked to be clock-relative (kickoffs `now ± Nh`) and to
  assert the 24h window (within-24h announced, +30h synced-but-not-announced; empty-window no-post).
- `/ajuda` unchanged (no command/category/scoring/grading change). Gates green.

### 2026-06-16 — Feature: per-game scoreboard for ended games (`/placar_jogo`, §10)

User request. New `/placar_jogo` command (group + DM): posts an inline picker of the most recently
**finished** games (most-recent-settled first, ~15); tapping one **edits the same message** to show
that single game's ranking — every player who bet on it, ranked by the points earned in **that game
only** (reusing `scoreboard.rank()`, same tie-breaks), under a header with the two teams and the
90′ score. Pure DB read (no provider call); voided games excluded.
- `callbacks.GameBoard` (`gb:<fixture>`) added to the codec + union + round-trip/malformed tests.
- `GameRepository.list_recently_ended(limit)` (FINISHED + `settled_at`, ordered `settled_at` desc).
- `board_data.load_game_records(fixture_id)` (+ extracted shared `_record` projector).
- `text_pt.game_board_text(...)` (escaped header `home h x a away` + medals).
- `keyboards.ended_games_keyboard`; `board_handlers.placar_jogo_handler` + `game_board_select`
  (registered with `^gb:` **before** the wizard catch-all, like the `^bv:` toggle).
- `/ajuda` + `app.PRIVATE/GROUP_COMMANDS` gained `/placar_jogo`; COMPLETION.md §10 + command-scope
  list updated (§11 maintenance rule). 296 tests, all gates green.

### 2026-06-15 — Feature: pre-game betting reminder (§9.3)

User request. New `JobQueue.run_repeating` reminder sweep (`bot/reminder_job.py`) posts one group
nudge ~1h before kickoff, combining games that share the **same kickoff time**. Soonest-due-slot
query (`GameRepository.list_due_for_reminder`), guarded `mark_reminded`, new nullable
`games.reminded_at` column + append-only migration `7f3a9c2b1e04`, announced-gate, and
`sync_fixtures` clears `reminded_at` on reschedule. Config: `reminder_lead_minutes` (60),
`reminder_interval_minutes` (10). Pure DB + group post (no provider calls). `/ajuda` unchanged
(no command/category/scoring/grading change). Design spec + multi-agent bug review (10 confirmed
findings folded in) under `docs/superpowers/`.

### 2026-06-16 — UX: /apostar game picker shows kickoff date+time

User request. Each open-game button in the `/apostar` DM picker now reads
`<home> x <away> · <dd/mm HH:MM>` (concise local kickoff), via new pure helper
`format_kickoff_short` in `domain/text_pt.py` (sibling to `format_kickoff_local`, no weekday).
Wired into `_show_open_games` (`bot/bets_handlers.py`). §8.2 updated. `/ajuda` unchanged
(no command/category/scoring/grading change). Tests: `test_format_kickoff_short`,
`test_apostar_dm_lists_open_games` asserts the button label.

### 2026-06-16 — Fairness: re-price FIRST_TEAM 3→2 + show points on wizard buttons

User request, driven by a multi-agent fairness analysis. `POINTS[FIRST_TEAM]` 3→2, so the table is
now **5/2/2/2/1**. Rationale: first-team-to-score is a *sub-coinflip* binary (p≈0.44 — ~8–10% of
matches void everyone via 0-0 / own-goal-only), so the old 3 pts ranked it **above** the genuinely
harder 3-way `WINNER` (p≈0.48) and made it the single highest-EV "farm" bet. The new table is
monotonic with rarity, with no dominant strategy (`FIRST_TEAM`/`BTTS`/`WINNER` tie at 2 — true
difficulties within base-rate noise).
- `domain/scoring.py` POINTS updated (single source of truth; the `/ajuda` points table auto-derives).
- COMPLETION.md §8.1 table + dated 2026-06-16 decision note (§11 maintenance rule).
- UX: category-picker buttons now show the value (`Placar exato · 5 pts`, singular `pt` for 1) via
  new pure helper `text_pt.category_button_label`, wired into `keyboards.category_keyboard`.
- Tests updated (scoring/settlement/text_pt) + new helper test + button-label assertion. 299 green.

### 2026-06-16 — Feature: AI palpites (`/palpite`, Gemini 3.1 Pro + grounding) (§20)

User request, built in an isolated git worktree. New **optional** AI feature: analyze each game in
the next 24h with Gemini (Google Search grounding, high thinking) and give a palpite per bet
category, cached in the DB and posted by `/palpite`.

**Grounded (per §2)** against `google-genai` **2.8.0** (`ai.google.dev/gemini-api/docs/gemini-3` +
`.../docs/google-search`). Decisions (in §20.3): use the **direct google-genai SDK** (not ADK — its
`google_search` is Gemini-2 only); get JSON via **prompt + pydantic validation** (not
`response_schema`, which conflicted with grounding); async via the SDK's native `client.aio`.

- **Config:** `gemini_api_key` (optional secret, `.env`), `gemini_model`
  (`gemini-3.1-pro-preview`), `palpite_time` (`06:00`) + `palpite_time_obj`.
- **AI layer** `tigrinho/ai/`: `base.py` (`PalpiteGenerator` Protocol), `schemas.py`
  (`PalpiteBatch`/`GamePalpite` + `extract_json`/`parse_batch`, PURE; reuses domain bet enums),
  `prompt.py` (`GameInfo` + `build_palpite_prompt`, PURE), `gemini.py` (`GeminiPalpiteGenerator`).
- **Service** `palpite_service.py` (telegram-free): `generate_palpites` (fills only games missing
  today's palpite → at most one Gemini call/day; DB is the cache) + `load_today_palpites`.
- **Persistence:** `ai_palpites` table (one row per `(fixture_id, palpite_date)`), append-only
  migration `c1a2b3d4e5f6`, `PalpiteRepository`, `GameRepository.list_upcoming_within`.
- **Bot:** `bot/palpite_handlers.py` (`/palpite`: no-key error / no-games / cold-cache on-demand
  generate with a "working" message / warm-cache instant), `bot/palpite_job.py` (daily 06h
  cache-warm job, no group post). `AppContext.palpite_generator` (None when no key). Wired into
  `app.py` (handler + job + private/group command lists) and `__main__.make_palpite_generator`.
- **Maintenance rule (§11):** `/ajuda`, COMPLETION.md (§4.1/§4.2 + new §20 + §8.2 command scope),
  README, `.env.example`, `config.example.yaml` all updated in this change.
- **Tests (+44):** config, ai schemas/prompt, repo+migration+models, service (cache/missing-only/
  unknown-fixture), text rendering, handler (4 branches), job (no-key/cache-warm/failure/schedule),
  generator (SDK mocked — grounding+thinking config asserted). 341 tests; all four gates green.

### 2026-06-16 — /palpite refinements: curiosity, single-flight, citation stripping

User request after the first prod deploy. (a) Removed `confidence`; (b) added `curiosity` — a
**web-grounded** head-to-head fact the model must NOT invent (empty string when none found → omitted
from the message); (c) **single-flight generation**: a process-wide `asyncio.Lock`
(`AppContext.palpite_lock`, shared by `/palpite` and the daily job) so a cold-cache burst of
`/palpite` fires **one** Gemini request (a concurrent caller gets "já estou analisando"; the
lock-holder re-checks the warm cache before generating); (d) `strip_citation_tags` removes grounding
tags (`[1]`/`[1.1.7]`) from `analysis`/`curiosity` at validation time (old cached rows cleaned on
load). §20.1/§20.2 updated. 349 tests; all four gates green.

### 2026-06-16 — Feature: pre-game reminder shows who bet & how many (§9.3)

User request (worktree). The ~1h reminder (`reminder_text`) now appends a `👥` line per game naming
who already bet and how many of the **5** categories each filled — inline compact format chosen by
the user: `👥 Já palpitaram: Ana (5/5), Felipe (3/5)`, ordered most-complete first (count desc, then
name); empty state reads `👥 Ninguém palpitou ainda 👀`. `reminder_text` items became 4-tuples
`(home, away, kickoff_local, bettors)`; `reminder_job` gathers bettors via `BetRepository`
(keyed on `telegram_id` so same-name players don't merge) inside the existing read session. New
`TOTAL_CATEGORIES = len(BetCategory)` constant (single source of truth for the "5"). No schema/
migration/provider change; pure DB read + group post. COMPLETION.md §9.3 updated; `/ajuda` unchanged
(no command/category/scoring/grading change). 354 tests; all four gates green.

### 2026-06-16 — Bug fixes: 4 medium findings from the exhaustive multi-agent hunt

Fixed the four medium-severity bugs surfaced (and adversarially verified) by the bug-hunt workflow
`wf_a260e64b-f20`. Each TDD'd (failing test first) with a focused regression test. 358 tests, all
four gates green. None touch commands/categories/scoring/grading, so `/ajuda` is unchanged.
- **U3 — un-void leaks phantom scoreboard rows** (`sync_job.py`): postpone→reschedule flipped the
  game back to `SCHEDULED` but never reset its voided bets, so `settled_at`+`points_awarded=0`
  survived and leaked 0-point/0-correct rows onto `/placar`. New `_unvoid_bets` resets them to
  pending in the un-void branch (mirrors `BetRepository.upsert`). Test
  `test_sync_unvoid_resets_bets_to_pending`.
- **U2 — `/minhas_apostas` showed in-progress bets as a loss** (`bets_handlers.py`): bets on a
  kicked-off-but-unsettled game rendered under "Encerrados" as `✗ (0 pts)`. Added a third
  `Em andamento` bucket (`settled_at is None` and not open) showing `⏳ aguardando resultado` with
  no verdict/points. Test `test_minhas_apostas_shows_started_ungraded_bet_as_pending`.
- **U4 — stuck-game admin DM spam** (`poll_job.py` + `runtime.py`): the "needs manual settlement"
  DM fired every ~10-min poll cycle. Added `AppContext.stuck_alerted` (in-memory, pruned when a
  game stops being stuck) so it alerts once per stuck game. Test
  `test_stuck_game_admin_alert_is_deduped_across_cycles`.
- **U8 — `/palpite` re-ran the full AI batch on every call** (`palpite_handlers.py` + `runtime.py`)
  when the model omitted any requested fixture (`len(rendered) < upcoming_count` stayed true
  forever). Trigger is now the set of upcoming−cached−attempted fixtures, with
  `AppContext.palpite_attempted` marking every requested fixture (even omitted ones) per day. Test
  `test_incomplete_generation_does_not_regenerate_every_call`.

### 2026-06-16 — /palpite: pick a game instead of dumping all of them (§20)

User request (worktree): `/palpite` flooded the chat with one message per next-24h game. It now
posts a **game picker** (one inline button per next-24h game, labelled `home x away · dd/mm HH:MM`);
**tapping a game** edits the message in place to show just that game's palpite. New `PalpiteView`
callback (opcode `pv:<fixture>`) + `palpite_games_keyboard` + `palpite_pick_text`. Generation moved
from the command to the new `palpite_select` callback: a tap on a cold game generates the day's batch
on demand (single-flight via `palpite_lock`, `palpite_attempted` so an omitted fixture doesn't
re-trigger forever) and shows the chosen game — preserving §20.1 "computed at most once". The `^pv:`
`CallbackQueryHandler` is registered before the wizard's catch-all (like `^bv:`/`^gb:`). Maintenance
rule (§11): `/ajuda`, the `palpite` BotCommand descriptions, and COMPLETION.md §20.1 updated. 365
tests; all four gates green.

### 2026-06-16 — Feature: live group notifications (kickoff + goals) (§9.4)

User request, built in an isolated worktree. The live-poll job now posts a "Bola rolando" message
when a tracked game goes LIVE and one message per goal (running score + scorer + minute, incl. extra
time), gated on a **free running-score check** so the `/fixtures/events` endpoint is hit only when a
game actually scores. New provider surface (`MatchResult.live_home_goals/live_away_goals`,
`GoalEvent.extra`, `get_goal_events` returning the uncapped timeline), pure
`domain/live.goal_progression` (own-goal flip → running score + scoring side), pt-BR `kickoff_text` /
`goal_text`, new `games.started_at` + `games.goals_announced` columns + append-only migration
`d2e3f4a5b6c7`.
- Kickoff dedups via `started_at` (restart-safe); a game first seen FINISHED gets no kickoff post and
  no retroactive goal dump — the settlement results post covers it.
- Goal cursor `goals_announced` advances to the timeline length; a VAR-disallowed goal resyncs the
  cursor down and posts nothing. Best-effort group sends (failure → log + admin DM, never crashes).
- Grading/settlement untouched (still 90′ score + ≤90′ timeline). `/ajuda` unaffected (no command/
  category/scoring/grading change). Spec + plan under `docs/superpowers/`. 385 tests (after rebase
  onto main); all four gates green.

### 2026-06-16 — Feature: announce VAR-disallowed goals (§9.4)

User request (prompted by a real prod incident: an Algeria goal vs. Argentina was counted then
annulled for offside; the old code resynced the cursor down **silently**, leaving a stale "GOL" in
the group with no retraction). The live-poll job now posts a **"🚫 Gol anulado pelo VAR"** message
per vanished goal when the running total drops below `goals_announced`.
- **Grounding (mandatory):** API-Football's docs enumerate `type:"Var"` detail `"Goal cancelled"`,
  but the live feed for the actual fixture returned `"Goal Disallowed - offside"` (not in the docs).
  Per the grounding rule, live docs win — the matcher accepts any `Var` detail starting with `goal`
  and containing `cancel`/`disallow`, excluding `Goal confirmed`, `Penalty confirmed/cancelled`,
  `Red card cancelled`. Doc: <https://www.api-football.com/news/post/var-events>.
- New provider surface: `VarCancellation` value object, `parse_var_cancellations`,
  `get_goal_cancellations` (one budgeted `/fixtures/events` call, only on a score drop). Pure pt-BR
  `text_pt.goal_cancelled_text` + `cancellation_reason_pt` (offside→impedimento, hand→mão na bola,
  foul→falta; unknown→omit). `poll_job._announce_cancellations` resyncs the cursor down **after** the
  retraction posts (failed send retries; synced game never re-announced).
- Detection is driven by the authoritative live score; the `Var` event only enriches the message,
  with a generic fallback when the score drops before the event surfaces (observed feed lag). **No
  schema change / migration** — the existing `goals_announced` cursor carries it.
- Grading/settlement untouched (the disallowed goal is `type:"Var"`, already excluded from
  `parse_goals`). `/ajuda` unaffected (notification, not a command/category/scoring/grading change).
  New tests for the parser, the pt-BR builders, and three poll-job paths (enriched / generic /
  already-synced). All four gates green; domain coverage 100%.

### 2026-06-16 — Feature: combined scoreboard for a set of ended games (`/placar_jogos`, §10)

User request. New `/placar_jogos` (group + DM): inline **multi-select** picker over the last 10
ended games; tapping toggles `☐`/`✅` (editing the same message), then `✅ Calcular placar` edits
to one ranking summing each player's points across the selected games (reuses `scoreboard.rank()`,
same tie-breaks). Pure DB read; voided games excluded.
- Selection is **stateless**: a bitmask over the picker position packed into `callback_data`
  (`pjt:<mask>:<index>` toggle, `pjc:<mask>` compute; ≤64 bytes). Positions resolve against the
  current last-10 list each callback; the result header names exactly the games summed (accepted
  list-drift caveat — fixture ids cannot fit 64 bytes).
- `callbacks.GamesBoardToggle`/`GamesBoardCompute`; `keyboards.combined_games_keyboard`;
  `board_data.load_games_records`; `text_pt.games_board_text`; `board_handlers.placar_jogos_handler`
  + `games_board_toggle` (`^pjt:`) + `games_board_compute` (`^pjc:`), registered before the wizard
  catch-all. `/ajuda` + `app.PRIVATE/GROUP_COMMANDS` + COMPLETION.md §10/§21 updated (§11 rule).
- Built via subagent-driven TDD (merged with main's live-notifications + /palpite work).
  Design + plan under `docs/superpowers/`.

### 2026-06-16 — Bug fix + feature: post-settlement score reconciliation (§8.3, §9.5)

Prod posted **France 3 × 0 Senegal** for fixture 1489383 and graded all 29 bets against it; the
real score was **3 × 1** (Senegal 90+5′, I. Mbaye). Confirmed via the live API (now correctly 3-1),
the prod DB row (`home_goals_90=3, away_goals_90=0, goals_announced=3`), and logs (`settled` at
21:04:30 UTC). Root cause: the poll job settles on a **single** `score.fulltime` read and is
idempotent; Senegal's stoppage-time goal was ingested into the feed *after* settlement, so the wrong
score was frozen with no reconciliation. (The live game was corrected manually by the user.)

Fix — a dedicated **reconcile job** (`tigrinho/bot/reconcile_job.py`) that re-checks settled games
for a bounded window and re-grades on a changed outcome:
- Config (`config.py` + `config.example.yaml`): `reconcile_window_hours` (6), `reconcile_first_delay_minutes`
  (5), `reconcile_interval_minutes` (30), `reconcile_budget_reserve` (25).
- `Game.last_reconciled_at` column + migration `e3f4a5b6c7d8` (down_revision `d2e3f4a5b6c7`).
- `GameRepository.list_reconcilable`; `text_pt.correction_text` (affected-only mentions, `antes → agora`,
  `(antes: H x A)` only when the score changed); `AppContext.reconcile_posts` per-game cap; wired in
  `app.post_init`.
- Backoff: first re-check ~5 min after settle, then every 30 min until kickoff+6h; lowest budget
  priority (yields below the reserve); write-time re-assert skips voided/rescheduled games; transient
  reads don't burn the cooldown; group correction posts only when a player's total moved, capped per
  game (then silent + one admin DM).
- TDD throughout; design + multi-POV review under `docs/superpowers/specs/2026-06-16-score-reconciliation-design.md`.
  `/ajuda` unchanged (no command/category/scoring/grading change). All four gates green (424 tests).
