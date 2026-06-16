# TigrinhoDaCopa (Telegram) — Build Specification

A **Telegram** bot for friendly (no real money) bets on **FIFA World Cup 2026** games, played
inside a group of friends. This document is the single source of truth for an autonomous
implementation loop (a "Ralph Wiggum" loop). It is written to be unambiguous: requirements use
**MUST / SHOULD / MAY**, and every feature states its inputs, outputs, and grading rules.

> This is the Telegram adaptation of the original Discord spec (the `COMPLETION.md` in the sibling
> `BotDoBolao` project). The **domain** (grading/scoring/settlement), the **provider** layer
> (API-Football), the **request budget**, and the **database** are interface-agnostic and carry over
> essentially unchanged. Only the **bot interaction layer** and the **config secrets/IDs** are
> rewritten for Telegram. Where this document differs from the Discord spec, **this document wins**
> for the Telegram build.

---

## 0. Autonomous build loop (Ralph Wiggum) — operating manual (MUST follow)

This project is built by re-feeding the **same** build prompt to the agent repeatedly. The agent
does not talk to itself; each iteration it sees its **own prior work in the files and git
history** and pushes the build one increment further. The whole point of this spec being
unambiguous is so the loop **never needs a human to make a judgment call mid-run** — every design
decision has already been made here.

**The iteration contract (every loop, in order):**

1. **Read state.** Read `PROGRESS.md` (the live checklist, see below) and `git log --oneline -20`
   to learn what is already done. Trust the files over memory.
2. **Pick exactly one next increment.** The smallest shippable slice of the **lowest-numbered
   unfinished milestone** (§18). Do not start milestone N+1 while milestone N is unfinished.
3. **Ground it first (if it touches an external API/library).** Web-search the **current** docs
   for `python-telegram-bot`, API-Football v3, SQLAlchemy 2.0 / Alembic, pydantic-settings, Typer
   **before** writing the code (see §2). Record the doc URL in a comment next to the code.
4. **Implement** the increment with strong typing.
5. **Run the gates** locally: `ruff check . && ruff format --check . && mypy --strict . && pytest`.
6. **If any gate is red, STOP adding scope and fix it first.** A red repo is the only priority.
7. **Commit** a small, focused commit with a clear message (e.g. `M5: sync job — consolidated
   announcement w/ deep-link buttons`). **Every commit MUST leave the repo green.**
8. **Update `PROGRESS.md`** — tick the finished item, note anything discovered.
9. Repeat.

**`PROGRESS.md` (the loop's memory) — MUST exist and be maintained.** A checked list mirroring the
milestones (§18) and their sub-tasks, each with an explicit, machine-checkable "done" line. The
agent updates it every iteration so the next iteration starts from a known state. It is committed
with the code.

**Definition of Done (DoD) for the whole build — emit the completion promise ONLY when ALL hold:**
- Every milestone M0–M11 in §18 is checked in `PROGRESS.md`.
- `ruff check .`, `ruff format --check .`, `mypy --strict .`, and `pytest` all pass.
- Domain logic (`scoring.py`, `settlement.py`) has ~100% line+branch coverage.
- A manual smoke test with `provider_mode: fake` runs end-to-end (sync → bet via deep-link →
  settle → results → board) without error.
- `README.md` lets a brand-new operator deploy from zero (§15.1).

When and only when the DoD holds, output exactly:

```
<promise>TIGRINHO_TELEGRAM_COMPLETE</promise>
```

**Guardrails / STOP conditions (the loop MUST NOT):**
- Commit secrets: never commit `.env` or a real `TELEGRAM_BOT_TOKEN` / `API_FOOTBALL_KEY`.
- Delete or truncate the SQLite DB, the `/data` volume, or existing Alembic migrations to "fix" a
  problem. Migrations are append-only.
- Make a gate pass by **weakening it** — never add blanket `# type: ignore`, never set `Any` to
  dodge mypy, never delete/`xfail`/skip a test or lower coverage thresholds to go green.
- Mark a milestone done without its tests written and passing.
- Loop forever: honor `--max-iterations`. If blocked on something genuinely outside the spec, do
  **not** emit the completion promise — instead write the blocker into `PROGRESS.md`, stop cleanly,
  and surface it in the final message.
- Invent product behavior. If something is truly unspecified, choose the simplest option consistent
  with §2's design priorities, **write the decision into this file**, and continue.

**Recommended invocation:**
```
/ralph-loop "Build TigrinhoDaCopa (Telegram) per COMPLETION.md, one milestone increment
per iteration; run all gates and commit each green increment; keep PROGRESS.md current; ground
every external API in current docs via web search first. Emit <promise>TIGRINHO_TELEGRAM_COMPLETE</promise>
only when the Definition of Done in §0 fully holds." --completion-promise "TIGRINHO_TELEGRAM_COMPLETE" --max-iterations 60
```

**Why this works:** small increments + green-on-every-commit + a persistent checklist make each
iteration's starting state clean and legible. Small, focused modules (§2) also keep each unit
inside the agent's context window, which makes its edits reliable.

---

## 1. Product summary

A small friend group (the "Tigrinhos") predicts World Cup match outcomes in their Telegram **group
chat**. The bot announces newly-scheduled games **to the group**, collects predictions **privately
in each player's 1:1 chat with the bot**, automatically grades them when each game ends, awards
points, and keeps an all-time and a weekly scoreboard. No money, no payments — just bragging rights.

**Anyone in the group can place bets.** There is no role or subscription system — every member of
the group already receives the bot's announcements, results, and scoreboards because the bot posts
them to the group.

**Why betting happens in private (DM):** Telegram has **no ephemeral (self-only) messages** inside a
group — every message and keyboard is visible to everyone. To keep predictions secret until kickoff
and to keep the group chat clean, **all bet placement/editing happens in the player's private chat
with the bot**, reached by tapping a button on the group announcement (a Telegram deep-link). The
group only ever sees the bot's own announcement, result, and scoreboard messages.

**Design priorities, in order:** correctness of grading → great usability (UX) → operability
(easy to debug/run) → simplicity.

---

## 2. Engineering principles (MUST follow)

- **Language/runtime:** Python **3.12+**.
- **Strong typing everywhere.** `mypy --strict` MUST pass with no `Any` leaks in domain code.
  Prefer `Enum`, `dataclass`/Pydantic models, and `typing.Protocol` over loose dicts.
- **Fail fast.** Validate all configuration at startup and crash with a clear message if anything
  required is missing or malformed. Never silently swallow exceptions in core flows.
- **Pure domain logic.** Bet grading, scoring, and settlement MUST be pure functions over value
  objects (no I/O, no clock, no DB). This makes them exhaustively unit-testable. **The domain layer
  is platform-agnostic and identical to the Discord build.**
- **Deterministic & idempotent.** Re-running settlement for a game MUST reproduce identical results.
  The scoreboard MUST be fully rebuildable from stored bets + match results.
- **Small, focused modules.** Each unit has one clear purpose and a documented interface.
- **Quality gates (CI-style, run locally):** `ruff` (lint + format), `mypy --strict`, `pytest`.
  Domain logic (scoring/settlement) MUST have ~100% line+branch coverage.
- **Ground every external API in current docs (web search is MANDATORY).** Before writing or
  changing any code that touches an external API or third-party library surface — API-Football
  endpoints & response fields, **`python-telegram-bot` (PTB) interfaces** (Application, JobQueue,
  handlers, inline keyboards, `BotCommandScope`, deep-link `start` payloads, parse modes),
  SQLAlchemy 2.0 / Alembic, pydantic-settings' YAML source, Typer, etc. — the implementing agent
  MUST use **web search** to read the **current official documentation** and verify exact endpoints,
  parameters, response shapes, status codes, method signatures, and version compatibility. Never
  rely on memory or assumptions; field names and APIs drift. Record the doc URL in a comment/commit
  next to the integration. **If the live docs disagree with this spec, the live docs win** — follow
  them and update this document.

---

## 3. Technology choices (MUST use unless a blocker is found)

| Concern | Choice | Notes |
|---|---|---|
| Telegram library | **`python-telegram-bot` 21.x** (async) | `Application`, `CommandHandler`, `CallbackQueryHandler`, `ConversationHandler`, `InlineKeyboardMarkup/Button`. Deep-link `start` payloads for DM betting. |
| Transport | **Long polling** (`run_polling` / `Application` updater) | No public URL / webhook needed; ideal for the Docker self-host. |
| Scheduling | **PTB `JobQueue`** | `run_daily(time=…)` for the daily sync; `run_repeating(interval=…)` for live polling. No extra dependency. |
| HTTP client | `httpx.AsyncClient` | Async network I/O (provider calls). |
| Database | SQLite via **SQLAlchemy 2.0** (typed ORM, **synchronous**) | Local SQLite queries are sub-ms; sync keeps the bot and CLI sharing identical repository code. Wrap in `asyncio.to_thread` only if contention ever appears. |
| Migrations | **Alembic** | Run `alembic upgrade head` on container start. |
| Admin CLI | **Typer** | Typed CLI; run via `docker compose exec`. |
| Config | **pydantic-settings** + YAML | Secrets from `.env`; all other settings from `config.yaml` (loaded via `YamlConfigSettingsSource`). Merged into one validated `Settings`; fail-fast. |
| Logging | **structlog** (or stdlib + JSON formatter) | Structured logs to stdout. |
| Tests | **pytest** + **pytest-asyncio** | Fake provider + temp SQLite. |
| Packaging | `pyproject.toml` | `uv` or `pip`. |

> **Decision (2026-06-15, M4 — live docs win, §2):** `python-telegram-bot` **21.x is EOL**; the
> current stable line is **22.x** (22.8 at build time). The bot targets **PTB 22.x**
> (`python-telegram-bot[job-queue]`). The APIs used (Application builder, `post_init`, `JobQueue`,
> `CommandHandler`/`CallbackQueryHandler`/`ConversationHandler`, inline keyboards, `BotCommandScope`,
> deep-link `start` payloads, `ParseMode.HTML`) are unchanged in 22.x. Wherever this document says
> "21.x", read "22.x".

> Async split rationale: **network = async** (don't block the event loop), **local DB = sync**
> (trivially fast, simpler, shared with the CLI). PTB runs its own asyncio event loop; the JobQueue
> callbacks are coroutines.

**Telegram message formatting:** use **HTML parse mode** (`ParseMode.HTML`) everywhere — it avoids
MarkdownV2's punishing escape rules. Player mentions use HTML inline mentions
`<a href="tg://user?id=USER_ID">Name</a>`, which work even when the user has no `@username`.

**`callback_data` constraint:** Telegram limits inline-button `callback_data` to **64 bytes**. All
wizard state encoded into `callback_data` MUST stay compact (numeric ids + short opcodes, e.g.
`b:CAT:FIXTURE` or `sc:h:3`). Do not pack human-readable payloads into `callback_data`.

---

## 4. Configuration (secrets in `.env`, settings in `config.yaml`)

A single `Settings` object (pydantic-settings) is assembled from **two sources** and validated at
startup; the bot MUST refuse to start if any required value is missing or malformed.

- **Secrets** come from environment / **`.env`** (gitignored) — credentials only.
- **All other settings** come from **`config.yaml`** (non-secret), loaded via pydantic-settings'
  `YamlConfigSettingsSource`.
- The two sets are disjoint; if a key somehow appears in both, the environment wins.
- The location of the YAML file is taken from the **`CONFIG_PATH`** env var (default
  `./config.yaml`). This is the only non-secret value allowed in the environment.

### 4.1 Secrets — `.env` (gitignored; commit `.env.example`)

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from **@BotFather**. |
| `API_FOOTBALL_KEY` | yes | API-Football key. |

### 4.2 Settings — `config.yaml` (commit `config.example.yaml`)

| Key | Required | Default | Purpose |
|---|---|---|---|
| `group_chat_id` | yes | — | The single group chat the bot serves. Announcements, results, and scoreboards are posted here. (Negative integer for groups/supergroups.) |
| `admin_user_id` | yes | — | Telegram **user id** DM'd on errors/limits. The admin MUST press **Start** in the bot's private chat once so the bot is allowed to DM them. |
| `bot_username` | yes | — | The bot's `@username` (without `@`), used to build deep-links `https://t.me/<bot_username>?start=…`. MUST be verified against the live bot at startup (`get_me().username`); mismatch is a fail-fast error. |
| `provider_mode` | no | `api_football` | `api_football` or `fake` (local/dev). |
| `api_football_base_url` | no | `https://v3.football.api-sports.io` | Provider base URL. |
| `wc_league_id` | no | `1` | FIFA World Cup league id (verify against provider). |
| `wc_season` | no | `2026` | Season. |
| `timezone` | no | `America/Sao_Paulo` | Drives sync time, displayed kickoffs, weekly reset. |
| `sync_time` | no | `06:00` | Daily fixtures sync (local time). |
| `poll_interval_minutes` | no | `10` | Live-poll cadence during match windows. |
| `match_window_hours` | no | `3` | How long after kickoff a game stays "active" for polling before forcing a settle/alert. |
| `api_daily_cap` | no | `100` | Hard ceiling on provider requests per budget day. |
| `api_budget_reset_tz` | no | `UTC` | Timezone whose midnight resets the request counter (API-Football resets at 00:00 UTC). |
| `db_path` | no | `/data/tigrinho.db` | SQLite file path (mounted volume). |
| `log_level` | no | `INFO` | Log level. |
| `log_format` | no | `json` | `json` or `console`. |

Example `config.yaml`:
```yaml
group_chat_id: -1001234567890
admin_user_id: 123456789
bot_username: TigrinhoDaCopaBot
provider_mode: api_football
api_football_base_url: https://v3.football.api-sports.io
wc_league_id: 1
wc_season: 2026
timezone: America/Sao_Paulo
sync_time: "06:00"
poll_interval_minutes: 10
match_window_hours: 3
api_daily_cap: 100
api_budget_reset_tz: UTC
db_path: /data/tigrinho.db
log_level: INFO
log_format: json
```

Both **`.env.example`** and **`config.example.yaml`** MUST be committed; the real `.env` (and, if
the IDs are considered private, `config.yaml`) MUST be gitignored.

---

## 5. Architecture & module layout

```
tigrinho/
  __init__.py
  config.py            # Settings: load .env (secrets) + config.yaml (settings), validate, fail-fast
  logging.py           # structlog setup
  enums.py             # shared leaf enums (Stage, GameStatus) — no I/O/DB deps; see decision note below
  db/
    engine.py          # SQLAlchemy engine/session factory
    models.py          # ORM models (typed)
    repositories.py    # CRUD repos: players, games, bets, api_usage
    migrations/        # Alembic
  providers/
    base.py            # FootballProvider Protocol + value objects (Fixture, MatchResult, GoalEvent)
    api_football.py    # ApiFootballProvider (httpx) — maps API JSON -> value objects
    fake.py            # FakeProvider for tests/local (provider_mode: fake)
    budget.py          # RequestBudget — daily counter + hard stop at api_daily_cap
  domain/
    bets.py            # BetCategory enum, payload models, validation
    scoring.py         # points table + per-category grading (PURE)
    settlement.py      # grade all bets for a MatchResult (PURE)
    text_pt.py         # pt-BR message templates (HTML parse mode)
  bot/
    app.py             # PTB Application builder + handler/job registration + post_init config check
    sync_job.py        # daily fixtures sync + group announcements (deep-link buttons) + reschedule/void
    poll_job.py        # live polling + settlement + results messages
    bets_handlers.py   # /start (deep-link payload), /apostar wizard (ConversationHandler), /minhas_apostas, /jogos
    board_handlers.py  # /placar (inline geral<->semana toggle) + /placar_jogo (per-game board)
    help_handlers.py   # /ajuda, /start (no payload — welcome)
    keyboards.py       # InlineKeyboardMarkup builders (games, categories, score pad, first-team, board toggle)
    callbacks.py       # compact callback_data encode/decode helpers (<=64 bytes)
    alerts.py          # admin DM alerts + structured logs
  cli.py               # Typer admin CLI
tests/
docker/                # Dockerfile, entrypoint
docker-compose.yml
.env.example
config.example.yaml
pyproject.toml
PROGRESS.md            # Ralph-loop live checklist (see §0)
README.md
CLAUDE.md
```

> There is **no** `subscribe` handler and **no** role logic — Telegram has no roles, and the group
> membership itself is the notification list.

> **Decision (2026-06-15, M2):** `Stage` and `GameStatus` live in a dependency-free leaf module
> `tigrinho/enums.py` (not in `db/models.py`) so the **pure domain** (`scoring.py`) and the
> **provider value objects** can import them without pulling in SQLAlchemy. `db/models.py`
> re-exports them, so `from tigrinho.db.models import Stage, GameStatus` still works. The Alembic
> migration is unaffected (it hard-codes the enum strings).

---

## 6. Data model

SQLite tables (via SQLAlchemy models, created/evolved with Alembic).

**players**
- `telegram_id` INTEGER PK — the Telegram user id
- `display_name` TEXT — best available name (`first_name` [+ `last_name`], or `@username`)
- `created_at` TIMESTAMP (UTC)

A player row is **auto-created on the user's first bet** (or when they open the betting wizard via a
deep-link and place a bet). The scoreboard only includes users who have placed at least one bet.

**games**
- `fixture_id` INTEGER PK — **canonical id from the provider**
- `match_hash` TEXT — `sha256(f"{kickoff_iso}|{home_team_id}|{away_team_id}")`, a human-readable/dedup label only (NOT identity)
- `stage` TEXT — `GROUP` | `KNOCKOUT`
- `home_team_id` INTEGER, `home_team_name` TEXT
- `away_team_id` INTEGER, `away_team_name` TEXT
- `kickoff_utc` TIMESTAMP, `kickoff_local` TIMESTAMP (display)
- `status` TEXT — provider status normalized: `SCHEDULED|LIVE|FINISHED|POSTPONED|CANCELLED|VOID`
- `home_goals_90` INTEGER NULL, `away_goals_90` INTEGER NULL — **90′ result** (regulation incl. stoppage)
- `advancing_team_id` INTEGER NULL — for knockout winner grading
- `first_scorer_player_id` INTEGER NULL — player id of the first genuine (non-own-goal) 90′ scorer,
  taken from the goal-event timeline (recorded for display; not squad-backed)
- `announced_at` TIMESTAMP NULL, `settled_at` TIMESTAMP NULL

**bets**
- `id` INTEGER PK
- `fixture_id` FK → games
- `player_telegram_id` FK → players
- `category` TEXT — see §8.1
- `payload_json` TEXT — category-specific (validated against typed model)
- `created_at`, `updated_at` TIMESTAMP
- `is_correct` BOOLEAN NULL, `points_awarded` INTEGER NULL, `settled_at` TIMESTAMP NULL
- **UNIQUE(`fixture_id`, `player_telegram_id`, `category`)** — enforces one bet per category per game

Bets are closed purely by time (`now >= kickoff_utc`), independent of any API call.

**api_usage** (request budget)
- `budget_date` DATE PK (in `api_budget_reset_tz`)
- `count` INTEGER

> No subscription/notification table exists — the Telegram **group membership** is the audience.

---

## 7. Football data provider

> **Unchanged from the Discord build** — this layer is platform-agnostic.

### 7.1 Interface (provider-agnostic)

Define `FootballProvider` as a `Protocol` returning **value objects** (never raw JSON):

- `get_fixtures(window_hours: int) -> list[Fixture]` — upcoming WC fixtures within window.
- `get_live_results() -> list[MatchResult]` — **one** call returning every currently-live WC fixture.
- `get_match_result(fixture_id: int) -> MatchResult` — final result + goal timeline for one game.

Value objects (frozen dataclasses): `Fixture`, `MatchResult` (carries `home_goals_90`,
`away_goals_90`, ordered `goals: list[GoalEvent]`, `advancing_team_id`, `status`, `stage`),
`GoalEvent` (`minute`, `team_id`, `player_id`, `player_name`, `is_own_goal`, `is_penalty`).
(No squad endpoint/value object — the first-scorer market is team-based; see §8.1 decision.)

`ApiFootballProvider` implements this against API-Football v3. `FakeProvider` returns scripted
fixtures/results for tests and local development (selected via the `provider_mode` setting).

### 7.2 API-Football mapping (MUST be exact)

> ⚠️ **Ground this first.** The endpoint paths, field names, status codes, and the WC league id /
> season in §4 and §7 reflect prior knowledge and MUST be re-verified against the **current
> API-Football v3 documentation via web search** before implementing. If the live docs differ,
> follow them and update this section.

- **90′ score:** use `score.fulltime` (this is the regulation result; it **excludes** extra time).
  `score.extratime` / `score.penalty` are used **only** to derive `advancing_team_id`.
- **Stage:** `KNOCKOUT` if the fixture round is a knockout round (Round of 32/16, QF, SF, Final,
  3rd place); else `GROUP`.
- **Advancing team (knockout):** the side whose `teams.{home,away}.winner == true`.
- **Status normalization:** `NS/TBD → SCHEDULED`; `1H/HT/2H/ET/BT/P/LIVE → LIVE`;
  `FT/AET/PEN → FINISHED`; `PST → POSTPONED`; `CANC/ABD → CANCELLED`.
- **Goal timeline (first scorer):** from `fixtures/events`, take events where `type == "Goal"`,
  `detail ∈ {"Normal Goal","Penalty"}` (exclude `"Own Goal"` and `"Missed Penalty"`), and
  `time.elapsed <= 90` (stoppage included; ET goals have `elapsed > 90` and are excluded;
  penalty-shootout events excluded). The earliest such event is the first scorer.

### 7.3 Request budget (MUST — the hard limit the user requires)

A `RequestBudget` wraps every provider call:

1. Before each request, read today's count from `api_usage` (key = today in `api_budget_reset_tz`).
2. If `count >= api_daily_cap` (default **100**), **do not make the request**. Raise/return a
   `BudgetExceeded` signal; the caller skips the work, logs it, and the bot DMs the admin **once per
   budget day**.
3. On a successful request, increment the count atomically.
4. Counter resets automatically when the budget date rolls over.

**Call-priority when the budget is tight (highest first):**
`daily fixtures sync` → `settlement reads at full-time` → `live polling`.
Live polling is the first thing to throttle/skip. Bet **closing never consumes budget** (time-based).

**Budget estimate (typical 4-game day):** 1 sync + ~40 live polls + ~4 full-time result reads
≈ **~45 / 100**. No squad endpoint is called (the first-scorer market is team-based).

---

## 8. Feature 1 — Bets

### 8.1 Bet categories, payloads, and grading (PURE functions)

> **Unchanged from the Discord build.** All score-based grading uses the **90′ result**
> (`home_goals_90`, `away_goals_90`). `total90 = home_goals_90 + away_goals_90`.

| Category | `BetCategory` | Payload | Wins when | Points |
|---|---|---|---|---|
| Exact score | `EXACT_SCORE` | `{home:int, away:int}` | both equal the 90′ score | **5** |
| First team to score | `FIRST_TEAM` | `{sel: HOME\|AWAY}` | that team scores the first genuine (non-own-goal) goal within 90′ | **2** |
| Both teams to score | `BTTS` | `{sel: BOTH\|ONLY_HOME\|ONLY_AWAY\|NEITHER}` | the 90′ scoring pattern matches | **2** |
| Winner | `WINNER` | `{sel: HOME\|DRAW\|AWAY}` | see knockout rule below | **2** |
| Over/Under 2.5 | `OVER_UNDER` | `{sel: OVER\|UNDER}` | `OVER` ⇢ `total90 ≥ 3`; `UNDER` ⇢ `total90 ≤ 2` | **1** |

> **Decision (2026-06-15):** the first-scorer market is on the **team**, not the player
> (`FIRST_TEAM`, `{sel: HOME|AWAY}`), and all squad infrastructure (the API `/players/squads` pull,
> the `SquadPlayer` value object, the `squad_players` table + `SquadRepository`, the CLI `squads`
> commands, and the paginated squad keyboard) is removed. Reason: it eliminates the only per-team
> roster dependency and the seeding step, simplifying setup. Points dropped 4→3 since a 2-way team
> pick is much easier than naming the exact scorer. **(Later re-priced 3→2 — see the 2026-06-16
> decision below.)** The goal timeline (`/fixtures/events`) is still parsed
> (it determines which team scored first). `games.first_scorer_player_id` is still recorded from the
> goal event (no squad needed) for the record/results display.

> **Decision (2026-06-16):** `FIRST_TEAM` re-priced **3→2** for fairness. A multi-method analysis
> (empirical WC base rates, information-theoretic surprisal, fair-odds/equal-EV, game-design) found
> first-team-to-score is a *sub-coinflip* binary (p≈0.44 — ~8–10% of matches finish 0-0 / own-goal-only
> and void everyone), so the old 3 pts ranked it **above** the genuinely harder 3-way `WINNER` (p≈0.48)
> and made it the single highest-EV "farm" bet. The fair table is **5/2/2/2/1**
> (`EXACT_SCORE`/`FIRST_TEAM`/`BTTS`/`WINNER`/`OVER_UNDER`): points now move monotonically with rarity
> and no category dominates on expected points. `FIRST_TEAM`/`BTTS`/`WINNER` tie at 2 because their
> true difficulties sit within base-rate noise (~0.44–0.48); a manufactured spread would overstate it.

**Winner grading rule:**
- **Group stage:** compare to 90′ result — `HOME` if `home>away`, `DRAW` if equal, `AWAY` if `away>home`.
- **Knockout:** the official outcome is the **advancing team** (`HOME`/`AWAY`). A knockout is never a
  `DRAW`; a `DRAW` selection in a knockout always loses. **The bet UI MUST hide the `DRAW` option for
  knockout fixtures.**

**First-team grading rule:** find the first goal event with `is_own_goal == false` and `minute ≤ 90`
(own goals are skipped; the next genuine goal counts). The winning side is `HOME` if that goal's team
is the home team, `AWAY` if the away team. If there is no genuine 90′ goal (0-0, or only own goals),
**all** `FIRST_TEAM` bets on that game lose.

**Points table is centralized** in `domain/scoring.py` (single constant) so it is trivial to tune.

### 8.2 Placing/editing bets (UX — MUST be inline-keyboard-wizard-driven, in DM)

**All betting happens in the player's private chat with the bot.** Bot commands are registered with
the appropriate **`BotCommandScope`** (`/apostar`, `/minhas_apostas` are DM-relevant; `/jogos`,
`/placar`, `/placar_jogo`, `/ajuda` work in group + DM). Commands are in pt-BR.

**Entry points into the wizard:**

1. **Deep-link button on a group announcement.** Each open game in an announcement carries an inline
   **URL button** `🎯 Apostar` → `https://t.me/<bot_username>?start=bet_<fixture_id>`. Tapping it
   opens the user's private chat with the bot and sends `/start bet_<fixture_id>`. The `/start`
   handler parses the payload, auto-creates the player if needed, and jumps straight into the wizard
   for that fixture.
2. **`/apostar` in DM (no payload).** Shows an inline-keyboard list of **open** games (kickoff in the
   future, not started); each button reads `<home> x <away> · <dd/mm HH:MM>` (concise local kickoff,
   via `format_kickoff_short`); tapping a game enters the wizard for it.
3. **`/apostar` in the group.** Telegram has no ephemeral replies, so the bot replies with a short
   message + a deep-link button `👉 Apostar no privado` and does the actual flow in DM.

**The wizard (PTB `ConversationHandler` + `CallbackQueryHandler`, editing one message in place):**

1. **Choose game** (if not already fixed by the deep-link).
2. **Choose category** — inline keyboard of the 5 categories.
3. **Collect the category-specific input via inline keyboards** (no modals exist in Telegram):
   - **Exact score:** a **number-pad** keyboard — pick `home` goals (0–9, with a `+` for higher),
     then `away` goals; the message previews the building score.
   - **First team to score:** a two-button keyboard with the home and away team names; each selects
     `HOME`/`AWAY` (no squads required).
   - **Winner:** `HOME`/`DRAW`/`AWAY` buttons — the `HOME`/`AWAY` buttons show the **real team
     names** (not "Mandante"/"Visitante"); **DRAW hidden for knockout fixtures**.
   - **BTTS:** `BOTH`/`ONLY_HOME`/`ONLY_AWAY`/`NEITHER` — the two "only" options name the **real
     teams** (e.g. "Só o Brasil" / "Só o Argentina"), consistent with the first-team/winner keyboards.
   - **Over/Under:** `OVER`/`UNDER`.
4. **Confirm** → upsert the bet (respecting the one-per-category unique constraint). Editing an
   existing bet reuses the same flow and overwrites.
   - The wizard MUST show the player's **current bets for the chosen game** so editing is obvious.

All wizard state is encoded in compact `callback_data` (≤64 bytes; see §3) via `bot/callbacks.py`.

> **Decision (2026-06-15, M6):** the wizard is implemented with **stateless
> `CallbackQueryHandler`s** (a single dispatcher decoding `callback_data`), **not** a
> `ConversationHandler`. Because §8.2/§3 require *all* wizard state to live in `callback_data`, a
> conversation state machine would be redundant; the stateless design is simpler, has no per-user
> state to leak, and survives restarts. The two-tap exact score bakes the chosen home goals into the
> away pad's `callback_data` (`e:<fixture>:<home>:<away>`), so no transient state is kept anywhere.
> This satisfies §2 (correctness, simplicity). The deep-link `/start bet_<id>` entry and the five
> payload collectors (score pad, first-team selector, winner/BTTS/over-under selectors) are unchanged.

- **`/minhas_apostas`** (DM) — lists the caller's bets grouped by game (open vs settled), payloads
  rendered human-readably and, for settled games, ✓/✗ + points. Each **still-open** bet has an inline
  **🗑 Apagar** button (the CRUD "delete"); deleting an open bet is allowed, deleting/editing a
  started game's bet is rejected.
- **`/jogos`** (group or DM) — lists upcoming/open games, kickoff (in `timezone`), stage, and whether
  the caller has bet in each category (quick "what's left to predict" view). In the group it includes
  the per-game `🎯 Apostar` deep-link buttons.

**Closing:** a bet is open only while `now < kickoff_utc`. Any attempt to create/edit/delete a bet
for a started game MUST be rejected with a clear pt-BR message (answered via `answer_callback_query`
for button taps). Closing requires **no** API call.

### 8.3 Settlement & results

When a game becomes `FINISHED` (see §9), the bot:
1. Builds a `MatchResult` (90′ score, goal timeline, advancing team).
2. Grades **every** bet on that game via `domain/settlement.py` (pure), writing
   `is_correct`/`points_awarded`/`settled_at`.
3. Posts **one** results message to **`group_chat_id`**: the final 90′ score, the first team to
   score, and **each participating player mentioned** (HTML inline mention `tg://user?id=…`) with their total
   points from that game (and a per-category breakdown). Players with zero points are still
   acknowledged.
4. Marks the game `settled_at`. Settlement is **idempotent** (re-running corrects values).

---

## 9. Feature 2 — Games sync, polling & lifecycle

### 9.1 Daily fixtures sync (`sync_time`, default 06:00 `timezone`)

Implemented as a PTB **`JobQueue.run_daily`** job. Flow (1 provider call, highest budget priority):
1. Fetch WC fixtures for the next **48h** (window configurable internally).
2. For each fixture **with both real teams decided** (skip placeholders like "Winner Group A"/TBD):
   - **New** (`fixture_id` unseen) → insert. (Newly-discovered games are **not** announced as they
     appear — see step 3.)
   - **Rescheduled** (known `fixture_id`, kickoff changed) → update kickoff + `match_hash`, queue a
     re-announcement; existing bets remain valid (now tied to the new time).
   - **Postponed/Cancelled** (status) → set `status = VOID`, **void its bets** (no points), queue a
     player notification.
3. Send **one consolidated "next 24h" announcement** to the group: every still-unannounced
   `SCHEDULED` game with `now < kickoff_utc <= now + 24h`. Each game line carries its `🎯 Apostar`
   deep-link button. `announced_at` is set **only on a successful send** (so a failure is retried
   next morning) and dedups a game across mornings. Re-announcements and void notices are separate
   concise messages.

Announcement text is pt-BR (HTML), e.g.:
```
🐯 Jogos das próximas 24h — apostas abertas!
• Brasil x Argentina — Sáb 16/06 16:00
• França x Alemanha — Sáb 16/06 19:00

Toque em "🎯 Apostar" abaixo para palpitar no privado (fecha no apito inicial).
```
…followed by one `🎯 Apostar: <jogo>` inline button per game in the next 24h (deep-links into DM).

> Because everyone in the group is notified by the post itself, there is **no role ping** and no
> subscription concept.

### 9.2 Live polling & auto-settlement

A PTB **`JobQueue.run_repeating(interval=poll_interval_minutes*60)`** job:
1. Determine **active** games: `kickoff_utc <= now <= kickoff_utc + match_window_hours` and not yet
   settled. If none, **return without any API call**.
2. Otherwise make **one** `get_live_results()` call; update `status`/live scores.
3. For each game now `FINISHED`, run settlement (§8.3), fetching `get_match_result()` once for the
   goal timeline if needed.
4. All calls go through `RequestBudget`. If the cap is hit, skip polling, log, DM admin once/day.

**Stuck-game safeguard:** if a game is still unsettled past `kickoff + match_window_hours`
(missing/late provider data), DM the admin that it **needs manual settlement** via the CLI.

### 9.3 Pre-game reminders (`reminder_lead_minutes`, default 60)

A PTB **`JobQueue.run_repeating(interval=reminder_interval_minutes*60)`** job that nudges the group
to bet ~1h before kickoff. Each sweep:
1. Selects the **soonest due kickoff slot**: announced, unreminded, `SCHEDULED` games with
   `now < kickoff_utc <= now + reminder_lead`, narrowed to those sharing the soonest `kickoff_utc`.
   If none, **return without posting**.
2. Posts **one** consolidated reminder to the group (HTML, pt-BR), with one `🎯 Apostar` deep-link
   button per game in the slot — combining games that kick off at the **same time**.
3. Marks those games `reminded_at` **only on a successful send** (re-validated to skip games
   voided/rescheduled mid-flight); a failed send is retried on the next sweep and DMs the admin.

Makes **no provider calls** and is independent of the API budget (§7.3). Idempotent via
`reminded_at` (no double-posting across sweeps or restarts). On reschedule, `reminded_at` is cleared
(§9.1) so a moved game is reminded again before its new kickoff. Games at *different* kickoff times
each get their own reminder; only same-slot games are combined.

---

## 10. Feature 3 — Scoreboard

- **`/placar`** — posts the scoreboard. Defaults to **Geral**, with an inline button to toggle to
  **Semana** (and back); toggling **edits the same message** (Telegram's clean replacement for
  Discord's typed command option). It MAY also accept a text argument (`/placar semana`).
  - **Geral (full):** all-time points per player, descending. Lasts the whole tournament.
  - **Semana (weekly):** points from games whose **kickoff falls in the current Mon→Sun week in
    `timezone`**. Resets each Monday 00:00.
- Display: ranked list (top ~15) with medals for the top 3; if the caller is outside the top 15,
  append their own rank/points line.
- **Tie-break order:** (1) total points desc, (2) exact-score hits desc, (3) total correct bets desc,
  (4) earliest `players.created_at`.
- The board MUST be derivable purely from settled bets (so the CLI can rebuild it).
- **`/placar_jogo`** — per-game scoreboard for an **already-ended** game. Posts an inline picker of
  the most recently finished games (most-recent first, ~15); tapping one **edits the same message**
  to show that single game's ranking — every player who bet on it, ranked by the points they earned
  in **that game only** (same tie-break order), under a header naming the two teams and the 90′
  score. Works in the group and in DM. Derived purely from that game's settled bets (no provider
  call); voided games are excluded.

---

## 11. Feature 4 — Help (`/ajuda`)

`/ajuda` explains, in pt-BR: how the bolão works, every command, the bet categories with examples,
the **points table**, the knockout 90′ rule, the "bets close at kickoff" rule, and that **betting
happens in the bot's private chat** (reached by tapping `🎯 Apostar` on an announcement, or by
sending `/apostar` to the bot in private). `/start` with no payload shows a short welcome that points
to `/ajuda`.

**Maintenance rule (MUST be enforced via `CLAUDE.md`):** any change to commands, bet categories,
scoring, or grading rules MUST update `/ajuda` text **and** this `COMPLETION.md` in the same
change.

`CLAUDE.md` MUST also encode the **grounding rule** from §2 (web-search the official docs before using
or changing any external API), the secrets-in-`.env` / settings-in-`config.yaml` split, and the
**Ralph-loop operating manual** from §0.

---

## 12. Notifications (no subscription system)

There is **no `Tigrinhos` role and no opt-in/opt-out commands** in the Telegram build. Telegram has
no roles, and every member of the group already receives the bot's announcements, results, and
scoreboards because they are posted to `group_chat_id`. This deliberately removes the entire
subscription subsystem (`/inscrever`, `/sair`, the role-management permissions, and the related
DB/notification state) present in the Discord spec.

> If, later, a per-user "ping me" list is ever wanted, it would be a DB table of subscribed
> `telegram_id`s, mentioned individually via HTML inline mentions — **out of scope for this build.**

---

## 13. Feature 5 — Admin CLI (Typer)

Run inside the container: `docker compose exec bot python -m tigrinho.cli <command>`. The CLI shares
the repository + domain code with the bot. Required capability groups:

1. **CRUD games/bets/players** — list/show/create/edit/delete any record.
2. **Manual result & re-settle** — set/override a game's 90′ score + first team to score
   (`--first-team home|away`), then run (or re-run) settlement and scoring for that game. Idempotent.
3. **Force sync & budget** — trigger the fixtures sync on demand; print today's API request counter
   (and remaining budget). _(No squad seeding — the first-scorer market is team-based.)_
4. **Recalc board & DB dump** — rebuild standings from scratch from settled bets; export/dump the
   SQLite DB (or specific tables) for debugging.

It SHOULD also provide a small **`telegram-info`** helper that prints the bot's resolved
`@username`/id (via `get_me`) and echoes the configured `group_chat_id`/`admin_user_id`, to help the
operator confirm setup.

CLI output MUST be readable tables; destructive commands MUST require a confirmation flag.

---

## 14. Operability — logging & error alerts

- **Structured logs** (`structlog`) to stdout → visible via `docker compose logs`. Include fixture
  ids, counts, and budget usage on key events.
- **Admin DM alerts:** the bot DMs `admin_user_id` on important events — sync failure, **daily API
  cap reached** (once/day), a game that can't be auto-settled, and any unhandled error in a scheduled
  job. Alerts are concise and actionable. (The admin MUST have pressed **Start** in the bot's private
  chat once, or Telegram will reject the DM — this is checked best-effort at startup and surfaced.)
- Scheduled jobs MUST catch their own exceptions, log with context, alert, and keep running (one bad
  cycle never kills the bot). Register a PTB **error handler** (`application.add_error_handler`) as a
  backstop for handler exceptions.

---

## 15. Deployment (Docker + Compose)

- **Dockerfile:** `python:3.12-slim`, non-root user, dependencies from `pyproject.toml`. Entrypoint
  runs `alembic upgrade head` then launches the bot (`python -m tigrinho` / the PTB app).
- **docker-compose.yml:** one `bot` service, `env_file: .env`, `restart: unless-stopped`, a named
  volume mounting `/data` so `db_path: /data/tigrinho.db` persists, and a bind-mount of `config.yaml`
  (read-only) with `CONFIG_PATH=/app/config.yaml`. **No inbound ports are needed** (long polling).
- **Telegram setup:** create the bot with **@BotFather**, copy the **token**; add the bot to the
  group; the bot needs **no admin rights** for this design (it only posts its own messages and
  betting is in DM), and **privacy mode may stay enabled** (the bot still receives its slash commands
  and callback queries). Register commands via `setMyCommands` (programmatically at startup or via
  BotFather) with the right `BotCommandScope`s.
- **`.env.example`** and **`config.example.yaml`** committed with every secret/setting from §4.
- **`README.md` is a full deployment guide** for a brand-new operator — see the required outline in
  §15.1.

### 15.1 README — required contents (full deployment guide)

`README.md` MUST let someone with **no prior context** deploy the bot from zero, with every step
copy-paste runnable. Default language English (pt-BR acceptable if the team prefers). Required
sections, in order:

1. **Overview** — what TigrinhoDaCopa does (pt-BR World Cup 2026 friendly bets in a Telegram group),
   feature highlights, and a clear "no real money" note.
2. **Prerequisites** — Docker + Docker Compose; a Telegram account; a Telegram group; an API-Football
   account.
3. **Create the Telegram bot** — talk to **@BotFather**, `/newbot`, copy the **token**; set the bot
   name and description; (optionally) `/setcommands`; note the bot's `@username`.
4. **Group & IDs** — add the bot to your group; obtain the **`group_chat_id`** (e.g. via a helper bot
   like `@userinfobot`, or by temporarily logging `update.effective_chat.id`); obtain your own
   **`admin_user_id`**; **press Start in the bot's private chat** (admin and every player must do this
   once so the bot can DM them — players are prompted automatically by the deep-link).
5. **Get the API-Football key** — sign up, copy the key, note the free-tier daily limit, and how to
   **verify the WC-2026 league id & season** against the current API/docs.
6. **Configure** — `cp .env.example .env` (fill the two secrets) and `cp config.example.yaml
   config.yaml` (fill `group_chat_id`, `admin_user_id`, `bot_username`, adjust settings); include a
   reference table of every setting (or link to §4).
7. **Run** — `docker compose up -d --build`; migrations run automatically on start; `docker compose
   logs -f` to watch startup, confirm the bot connected (`get_me`) and registered its commands.
8. **First-run setup** — optionally force a sync to populate games (no squad seeding is needed; the
   first-scorer market is team-based).
9. **Player guide** — every command (`/apostar`, `/minhas_apostas`, `/jogos`, `/placar`, `/ajuda`,
   `/start`) with one-line descriptions, and the "tap 🎯 Apostar on an announcement to bet in
   private" flow.
10. **Admin CLI** — how to exec into the container and run each capability group, with examples.
11. **Operations** — where the SQLite DB lives (the `/data` volume) and how to back it up; reading
    logs; admin DM alerts; behavior when the daily API cap is hit; updating/redeploying.
12. **Troubleshooting** — common failures and fixes: bot not posting to the group (removed from
    group / wrong `group_chat_id`); deep-link doesn't open the wizard (wrong `bot_username`); admin/
    player not receiving DMs (didn't press Start); commands not appearing (`setMyCommands` scope);
    games not showing (wrong league id / season — re-verify via docs); API cap reached; timezone
    surprises.
13. **Development** — run locally with `provider_mode: fake`; run `pytest`, `ruff`, `mypy`.
14. **Disclaimer** — friendly bets only, no real money, not affiliated with FIFA.

---

## 16. Testing strategy

- **Domain (highest priority, ~100% coverage):** table-driven unit tests for every bet category,
  including edge cases — knockout 90′ draw, own-goal-first, 0-0 first scorer, advancing-team winner,
  over/under boundary at exactly 2 and 3 goals, BTTS NEITHER on 0-0. **(Identical to the Discord
  build — these are pure functions.)**
- **Settlement:** idempotency (running twice yields identical points) and full-game grading.
- **Repositories:** CRUD against a temp SQLite; the one-bet-per-category constraint.
- **Provider:** `ApiFootballProvider` JSON→value-object mapping with recorded fixtures (use the
  `score.fulltime` vs ET/penalty distinction as explicit cases). `RequestBudget` hard-stop at cap.
- **Config:** loading merges `.env` + `config.yaml`; missing required values fail fast; `bot_username`
  mismatch fails fast.
- **Bot flows:** thin; rely on `FakeProvider`. Cover: the **deep-link `/start bet_<fixture_id>`
  payload** parsing and player auto-create; the **wizard state transitions** (game → category →
  payload → confirm/upsert) for at least the score number-pad and the winner DRAW-hidden-for-knockout
  case; **time-based closing** rejection; the **active-window polling decision** (no API call when no
  active games); the **`callback_data` encode/decode** round-trip staying ≤64 bytes.

---

## 17. Rules summary (quick reference)

- Anyone in the group can bet; **betting happens in the bot's private chat** (via the `🎯 Apostar`
  deep-link or `/apostar` in DM). No roles, no subscription system.
- The group receives **announcements, results, and scoreboards** only — picks stay private until
  kickoff.
- One bet per category per game; all categories optional; editable until kickoff; closing is
  time-based (no API call).
- Score-based bets (exact score, BTTS, over/under, first team to score) grade on the **90′** result.
- Winner: group = 90′ 1X2 (draw allowed); knockout = advancing team (no draw; UI hides draw).
- First team to score = the team of the first genuine 90′ goal; own goals don't count; 0-0 or
  own-goal-only ⇒ all first-team bets lose.
- Over/Under 2.5: Over = total90 ≥ 3, Under = total90 ≤ 2.
- Canonical game id = provider `fixture_id`; reschedule updates in place; cancel ⇒ VOID + bets voided + notify.
- Hard stop at `api_daily_cap` (default 100) requests/day; priority sync > settlement > polling.
- Weekly board = current Mon→Sun in `America/Sao_Paulo`; full board = whole tournament.
- Secrets live in `.env` (`TELEGRAM_BOT_TOKEN`, `API_FOOTBALL_KEY`); every other setting lives in
  `config.yaml`.
- Ground every external API/library in current docs via web search **before** coding it (live docs win).
- Inline-button `callback_data` ≤ 64 bytes; messages use **HTML** parse mode.

---

## 18. Build order (milestones for the loop)

Each milestone is independently testable; do them in order (see §0 for the per-iteration contract).
Before any milestone that integrates an external API or library, **ground it first** — web-search the
current official docs (per §2) and code against them. After each milestone, ensure all gates are
green and update `PROGRESS.md`.

- **M0 — Scaffold:** `pyproject.toml`, ruff/mypy/pytest config, package layout, `config.py` (`.env` +
  `config.yaml` loading), `logging.py`, initial `PROGRESS.md`. Gates green on an empty app.
- **M1 — Data layer:** models (`players.telegram_id`), Alembic initial migration, repositories + tests.
- **M2 — Provider:** `base.py` value objects + Protocol, `FakeProvider`, `ApiFootballProvider`,
  `RequestBudget` + tests (mock httpx; budget hard-stop). **(Unchanged from Discord.)**
- **M3 — Domain:** `bets.py`, `scoring.py`, `settlement.py` (pure) + exhaustive tests (§16).
  **(Unchanged from Discord.)**
- **M4 — Bot skeleton:** PTB `Application` (long polling), `post_init` config validation (verify
  `get_me().username == bot_username`, can reach `group_chat_id`), `setMyCommands` with scopes,
  `/ajuda`, `/start` (welcome), error handler.
- **M5 — Sync job:** daily fixtures sync (`JobQueue.run_daily`), consolidated morning "next 24h"
  group announcement with per-game `🎯 Apostar` deep-link buttons, reschedule/void handling.
- **M6 — Bet handlers:** `/start bet_<fixture_id>` deep-link entry, `/apostar` wizard
  (`ConversationHandler` + inline keyboards: game → category → payload → confirm/upsert), score
  number-pad, first-team selector, DRAW hidden for knockout, `/minhas_apostas` (with delete),
  `/jogos`, time-based closing, `callback_data` codec.
- **M7 — Poll job:** active-window live polling (`JobQueue.run_repeating`), auto-settlement, results
  message to the group with player mentions, stuck-game admin alert.
- **M8 — Board:** `/placar` Geral with inline Geral↔Semana toggle, tie-breaks.
- **M9 — Admin CLI:** all four capability groups + `telegram-info` helper.
- **M10 — Deploy:** Dockerfile, compose, volume + config bind-mount, entrypoint migrations,
  `.env.example`, `config.example.yaml`, **full README (§15.1)**, `CLAUDE.md` (incl. §0 loop manual).
- **M11 — Hardening:** budget enforcement end-to-end, edge cases, coverage, manual smoke test with
  `FakeProvider`. When the §0 Definition of Done holds, emit
  `<promise>TIGRINHO_TELEGRAM_COMPLETE</promise>`.

---

## 19. Assumptions & defaults (override here if wrong)

- Single Telegram group (one `group_chat_id`). No multi-group support.
- Betting is **DM-only** (private chat with the bot), reached via deep-link or `/apostar` in DM. The
  group is used only for the bot's announcements/results/scoreboards.
- **No subscription/role system** — the group membership is the audience (§12).
- Admin actions are CLI-only (the bot exposes no admin commands; only DM alerts). Admin must press
  **Start** in the bot's DM once to be alertable; players are prompted to Start automatically by the
  deep-link.
- The bot needs **no group-admin rights** and privacy mode may remain enabled.
- `wc_league_id=1`, `wc_season=2026` for API-Football — **verify** against the live API before release.
- The first-scorer market is **team-based** (`FIRST_TEAM`: HOME/AWAY) — no squad data is fetched,
  stored, or seeded (see §8.1 decision).
- Messages use **HTML** parse mode; mentions use `tg://user?id=…` inline mentions; `callback_data`
  stays ≤ 64 bytes.
```
