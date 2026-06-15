# CLAUDE.md — TigrinhoDaCopa (Telegram)

Persistent memory for every Claude Code session in this repo. Keep it short; **point to the
spec, don't duplicate it.** `COMPLETION.md` section numbers (§) are referenced throughout.

## Project

A **Telegram** bot for friendly, **no-money** bets on **FIFA World Cup 2026**, played inside one
group of friends (the "Tigrinhos"). Announcements, results, and the scoreboard are posted to the
**group**; **all betting is DM-only** (Telegram has no ephemeral group messages), reached via a
deep-link button on each announcement. There are **no roles and no subscription system** — group
membership is the audience.

## Source of truth

- **`COMPLETION.md` is the authoritative spec.** When in doubt, it wins (except live external docs —
  see grounding rule).
- **`PROGRESS.md` is the live build checklist** mirroring the milestones (§18). It **MUST be updated
  every iteration**.
- **Read both before doing any work**, plus `git log --oneline -20`. Trust the files over memory.

## How to work here (Ralph loop) — see §0 for the full manual

This repo is built by re-feeding the same prompt to an autonomous loop. Per iteration:

- Do **one smallest shippable increment** of the **lowest-numbered unfinished milestone** (§18).
  Never start milestone N+1 while N is unfinished.
- **Ground first** if it touches an external API/library (see grounding rule).
- Implement with strong typing, then **run all gates** (below).
- **Commit only when green** — every commit MUST leave the repo green. Small, focused commits.
- **Update `PROGRESS.md`** (tick the item, note discoveries).
- **Guardrails (MUST NOT):** commit secrets; delete/truncate the SQLite DB, `/data`, or existing
  Alembic migrations (migrations are append-only); weaken a gate to go green (no blanket
  `# type: ignore`, no `Any` to dodge mypy, no deleting/`xfail`/skipping tests, no lowering coverage);
  mark a milestone done without passing tests; invent unspecified product behavior (pick the simplest
  §2-consistent option and **write the decision into `COMPLETION.md`**).
- If genuinely blocked outside the spec, **do not** emit the completion promise — record the blocker
  in `PROGRESS.md`, stop cleanly, and surface it.
- The whole-build completion promise (emit only when the §0 Definition of Done fully holds):
  <promise>TIGRINHO_TELEGRAM_COMPLETE</promise>

## Quality gates (run before EVERY commit)

```
ruff check .
ruff format --check .
mypy --strict .
pytest
```

All four MUST pass. **Domain logic (`domain/scoring.py`, `domain/settlement.py`) needs ~100%
line+branch coverage** and must stay pure (no I/O, no clock, no DB).

## Grounding rule (MANDATORY — §2, §11)

Before writing or changing **any** code that touches an external API or library surface, **web-search
the CURRENT official docs** and verify exact endpoints, signatures, response field names, and status
codes. Applies to: **python-telegram-bot 21.x**, **API-Football v3**, **SQLAlchemy 2.0 / Alembic**,
**pydantic-settings** (YAML source), **Typer**, **httpx**. Never rely on memory — APIs drift. Record
the doc URL in a comment next to the integration. **If live docs disagree with the spec, live docs
win** — follow them and update `COMPLETION.md`.

## Secrets split (§4) — NEVER commit `.env`, `config.yaml`, or the SQLite DB

- **Secrets → `.env`** (gitignored): `TELEGRAM_BOT_TOKEN`, `API_FOOTBALL_KEY` only.
- **Everything else → `config.yaml`** (loaded via pydantic-settings `YamlConfigSettingsSource`):
  `group_chat_id`, `admin_user_id`, `bot_username`, provider/timezone/budget settings, etc.
- The two sets are disjoint; env wins on collision. `CONFIG_PATH` (default `./config.yaml`) is the
  only non-secret env value. Commit `.env.example` and `config.example.yaml`.

## Maintenance rule (§11 — enforced)

Any change to **commands, bet categories, scoring, or grading rules** MUST update the **`/ajuda`
text** AND **`COMPLETION.md`** in the **same change**.

## Tech stack (§3) — module layout in §5

- Python **3.12+**; **python-telegram-bot 21.x** (long polling + `JobQueue`); **SQLAlchemy 2.0**
  (sync ORM) + **Alembic**; **httpx** (async, provider calls); **pydantic-settings** + YAML;
  **structlog**; **Typer** (admin CLI); **pytest** + **pytest-asyncio**.
- Split: **network = async** (don't block the loop), **local SQLite = sync** (sub-ms, shared with CLI).
- Strong typing (`mypy --strict`, **no `Any` in domain**), fail-fast config validation, pure domain
  logic, small focused modules.

## Telegram specifics to remember (§3, §8)

- **HTML parse mode everywhere** (`ParseMode.HTML`) — avoids MarkdownV2 escaping.
- **Inline-button `callback_data` ≤ 64 bytes** — pack only numeric ids + short opcodes (e.g.
  `b:CAT:FIXTURE`, `sc:h:3`); never human-readable payloads. Codec lives in `bot/callbacks.py`.
- **Betting is DM-only** via deep-link `https://t.me/<bot_username>?start=bet_<fixture_id>`; the
  `/start` handler parses the payload, auto-creates the player, and jumps into the wizard.
- **Mentions** use HTML inline `<a href="tg://user?id=USER_ID">Name</a>` (works without `@username`).
- **No roles, no subscription system** — the group post itself is the notification.

## Coding conventions

- Strong typing end-to-end (`mypy --strict`, no `Any` in domain). Prefer `Enum`, frozen dataclasses /
  Pydantic models, and `typing.Protocol` over loose dicts.
- **Pure domain logic** — no I/O, clock, or DB in `scoring.py` / `settlement.py`; deterministic and
  idempotent (re-settling reproduces identical results; the board rebuilds from stored bets + results).
- Small, single-purpose modules with documented interfaces.
- **Fail fast** — validate all config at startup; crash with a clear message on anything missing or
  malformed; never silently swallow exceptions in core flows.
