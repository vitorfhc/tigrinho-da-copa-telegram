# PROGRESS — TigrinhoDaCopa (Telegram)

**Status: M0 complete** (scaffold + config + logging green) · _Created 2026-06-15_

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

- [ ] Every milestone M0–M11 in §18 is checked in this file.
- [ ] `ruff check .` passes.
- [ ] `ruff format --check .` passes.
- [ ] `mypy --strict .` passes.
- [ ] `pytest` passes.
- [ ] Domain logic (`scoring.py`, `settlement.py`) has ~100% line+branch coverage.
- [ ] A manual smoke test with `provider_mode: fake` runs end-to-end (sync → bet via deep-link →
      settle → results → board) without error.
- [ ] `README.md` lets a brand-new operator deploy from zero (§15.1).

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

- [ ] **M1 — Data layer**
  - [x] ORM models (`players` keyed on `telegram_id`, `games`, `bets`, `squad_players`, `api_usage`)
        per §6, typed
  - [x] `db/engine.py` — SQLAlchemy 2.0 engine/session factory (synchronous)
  - [x] Alembic initial migration (`alembic upgrade head` works; migrations append-only)
        — rev `edbcfec84e20`; env.py resolves URL via `TIGRINHO_DB_URL`/`Settings.db_path`,
        `render_as_batch=True`; tested against ORM metadata + downgrade
  - [ ] `db/repositories.py` — CRUD repos: players, games, bets, squads, api_usage
  - [ ] Repository tests against a temp SQLite (incl. the `UNIQUE(fixture_id, player, category)`
        one-bet-per-category constraint)
  - **Done when:** models + initial migration + repositories exist, their tests pass, all gates green.

- [ ] **M2 — Provider** (interface-agnostic — unchanged from the Discord build)
  - [ ] `providers/base.py` — `FootballProvider` Protocol + frozen value objects (`Fixture`,
        `MatchResult`, `GoalEvent`, `SquadPlayer`)
  - [ ] `providers/fake.py` — `FakeProvider` (scripted fixtures/results) for `provider_mode: fake`
  - [ ] `providers/api_football.py` — `ApiFootballProvider` (httpx) mapping API-Football v3 JSON →
        value objects (90′ = `score.fulltime`; stage; advancing team; status normalization; goal
        timeline ≤90′ excluding own goals) — **ground against live docs first**
  - [ ] `providers/budget.py` — `RequestBudget`: daily counter keyed by `api_budget_reset_tz`, hard
        stop at `api_daily_cap`, atomic increment, `BudgetExceeded` signal
  - [ ] Provider tests (mock httpx; `score.fulltime` vs ET/penalty cases) + budget hard-stop test
  - **Done when:** the four provider modules + budget exist, their tests pass, all gates green.

- [ ] **M3 — Domain** (interface-agnostic — unchanged from the Discord build)
  - [ ] `domain/bets.py` — `BetCategory` enum + typed per-category payload models + validation
  - [ ] `domain/scoring.py` — centralized points table + per-category grading (PURE)
  - [ ] `domain/settlement.py` — grade every bet for a `MatchResult` (PURE, deterministic, idempotent)
  - [ ] `domain/text_pt.py` — pt-BR message templates (HTML parse mode)
  - [ ] Exhaustive table-driven domain tests (§16): knockout 90′ draw, own-goal-first, 0-0 first
        scorer, advancing-team winner, O/U boundary at exactly 2 and 3, BTTS NEITHER on 0-0,
        settlement idempotency
  - **Done when:** domain modules exist, tests pass, `scoring.py` + `settlement.py` have ~100%
    line+branch coverage, all gates green.

- [ ] **M4 — Bot skeleton**
  - [ ] `bot/app.py` — PTB `Application` builder (long polling) + handler/job registration
  - [ ] `post_init` config validation: verify `get_me().username == bot_username`, can reach
        `group_chat_id` (fail-fast on mismatch)
  - [ ] `setMyCommands` registered with the correct `BotCommandScope`s
  - [ ] `help_handlers.py` — `/ajuda` (pt-BR) and `/start` (no payload — welcome)
  - [ ] `application.add_error_handler` backstop
  - [ ] Bot-skeleton tests (rely on `FakeProvider`)
  - **Done when:** the app builds, `post_init` validation + `/ajuda` + `/start` welcome + error handler
    exist with tests passing, all gates green.

- [ ] **M5 — Sync job**
  - [ ] `bot/sync_job.py` — daily fixtures sync via `JobQueue.run_daily` (1 provider call, top budget
        priority), skipping placeholder/TBD fixtures
  - [ ] New game → insert + queue announcement; rescheduled → update kickoff + `match_hash` +
        re-announce (bets stay valid); postponed/cancelled → `status = VOID` + void bets + notify
  - [ ] One consolidated group announcement (pt-BR, HTML) with a per-game `🎯 Apostar` deep-link
        button (`https://t.me/<bot_username>?start=bet_<fixture_id>`)
  - [ ] Sync-job tests (new/reschedule/void paths) with `FakeProvider`
  - **Done when:** `sync_job.py` exists, its tests pass, all gates green.

- [ ] **M6 — Bet handlers**
  - [ ] `bot/callbacks.py` — compact `callback_data` encode/decode helpers (≤64 bytes) with round-trip
  - [ ] `bot/keyboards.py` — inline keyboard builders (games, categories, score pad, paginated squad,
        board toggle)
  - [ ] `bot/bets_handlers.py` — `/start bet_<fixture_id>` deep-link entry (parse payload, auto-create
        player, jump into wizard)
  - [ ] `/apostar` wizard (`ConversationHandler` + `CallbackQueryHandler`, editing one message):
        game → category → payload → confirm/upsert (respects one-per-category unique constraint)
  - [ ] Score number-pad input; paginated squad keyboard for first scorer; **DRAW hidden for knockout**;
        BTTS and Over/Under selectors
  - [ ] `/minhas_apostas` (DM, grouped open vs settled, ✓/✗ + points, 🗑 Apagar delete on open bets)
  - [ ] `/jogos` (group or DM; upcoming games + per-category bet status; group includes deep-link buttons)
  - [ ] Time-based closing: create/edit/delete on a started game rejected with clear pt-BR message (no
        API call)
  - [ ] Bet-flow tests (§16): deep-link payload parse + player auto-create; wizard state transitions
        (score pad, knockout DRAW-hidden); time-based closing rejection; `callback_data` codec ≤64-byte
        round-trip
  - **Done when:** `callbacks.py`, `keyboards.py`, `bets_handlers.py` exist, their tests pass, all gates
    green.

- [ ] **M7 — Poll job**
  - [ ] `bot/poll_job.py` — active-window live polling via `JobQueue.run_repeating`; returns with **no
        API call** when no active games
  - [ ] One `get_live_results()` call when active; update status/live scores; on `FINISHED` run
        settlement (§8.3) fetching `get_match_result()` once if needed; all calls via `RequestBudget`
  - [ ] One results message to `group_chat_id`: 90′ score, first scorer, each participating player
        mentioned (HTML `tg://user?id=…`) with points + per-category breakdown
  - [ ] Stuck-game safeguard: DM admin when a game is unsettled past `kickoff + match_window_hours`
  - [ ] `bot/alerts.py` — admin DM alerts + structured logs (cap-reached once/day, etc.)
  - [ ] Poll-job tests: active-window decision (no API call when none active) + auto-settlement path
  - **Done when:** `poll_job.py` (+ `alerts.py`) exist, their tests pass, all gates green.

- [ ] **M8 — Board**
  - [ ] `bot/board_handlers.py` — `/placar` posts the scoreboard, defaults to **Geral**, inline
        Geral↔Semana toggle that **edits the same message** (MAY also accept `/placar semana`)
  - [ ] Geral = all-time points desc; Semana = current Mon→Sun week in `timezone` (resets Monday 00:00)
  - [ ] Ranked top ~15 with medals for top 3; append caller's own rank/points if outside top 15
  - [ ] Tie-breaks: (1) points desc, (2) exact-score hits desc, (3) total correct desc, (4) earliest
        `players.created_at`
  - [ ] Board derivable purely from settled bets (CLI-rebuildable) + tests
  - **Done when:** `board_handlers.py` exists, its tests pass, all gates green.

- [ ] **M9 — Admin CLI**
  - [ ] `cli.py` (Typer) sharing repository + domain code; readable tables; destructive commands
        require a confirmation flag
  - [ ] Group 1: CRUD games/bets/players (list/show/create/edit/delete)
  - [ ] Group 2: manual result + re-settle (set/override 90′ score + first scorer, run/re-run
        settlement — idempotent)
  - [ ] Group 3: force sync; refresh/seed cached squads; print today's API request counter + remaining
        budget
  - [ ] Group 4: recalc/rebuild board from settled bets; export/dump the SQLite DB (or tables)
  - [ ] `telegram-info` helper (resolved `@username`/id via `get_me`; echoes `group_chat_id`/
        `admin_user_id`)
  - [ ] CLI tests
  - **Done when:** `cli.py` exists with all four capability groups + `telegram-info`, its tests pass,
    all gates green.

- [ ] **M10 — Deploy**
  - [ ] `docker/Dockerfile` — `python:3.12-slim`, non-root user, deps from `pyproject.toml`
  - [ ] `docker-compose.yml` — one `bot` service, `env_file: .env`, `restart: unless-stopped`, named
        `/data` volume, read-only `config.yaml` bind-mount, `CONFIG_PATH=/app/config.yaml`, no inbound
        ports
  - [ ] Entrypoint runs `alembic upgrade head` then launches the bot
  - [ ] `.env.example` and `config.example.yaml` committed with every secret/setting from §4
  - [ ] Full `README.md` per §15.1 (all 14 sections, copy-paste deployable from zero)
  - [ ] `CLAUDE.md` — grounding rule (§2), secrets/settings split (§4), maintenance rule (§11), and the
        §0 Ralph-loop operating manual
  - **Done when:** Dockerfile + compose + entrypoint + example config files + README (§15.1) +
    `CLAUDE.md` exist, image builds, all gates green.

- [ ] **M11 — Hardening**
  - [ ] Budget enforcement verified end-to-end (priority sync > settlement > polling; hard stop at cap)
  - [ ] Edge cases covered; domain coverage at ~100% line+branch for `scoring.py` + `settlement.py`
  - [ ] Manual end-to-end smoke test with `provider_mode: fake` (sync → bet via deep-link → settle →
        results → board) runs without error
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

**Next:** M1 — Data layer. Ground SQLAlchemy 2.0 typed ORM (`Mapped`/`mapped_column`) + Alembic
(env.py for offline/online, `alembic upgrade head`) before coding. Models keyed on
`players.telegram_id` (§6); enforce `UNIQUE(fixture_id, player_telegram_id, category)` on bets.
