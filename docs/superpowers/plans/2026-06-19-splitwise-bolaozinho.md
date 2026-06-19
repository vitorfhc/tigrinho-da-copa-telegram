# Splitwise Auto-Registration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a bolãozinho finishes, mirror its result into a shared Splitwise group as one balanced expense (losers owe their stake, winner(s) credited), with email/account linking required at join for feature-era ("AUTO") bolãozinhos and an admin-triggered path for pre-existing ("MANUAL") ones.

**Architecture:** A pure ledger module (`domain/splitwise_ledger.py`, 100% covered) computes per-user paid/owed cents; an async httpx client (`providers/splitwise.py`) talks to Splitwise; a service (`splitwise_service.py`) decides create/update/skip from DB state; the bot layer fires registration best-effort from `resolve_and_post` (AUTO) and the sweep, and a wizard handles linking. A per-bolão `splitwise_mode` enum (AUTO/MANUAL/EXCLUDED) carries the policy.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 + Alembic (batch ALTER), httpx (async), python-telegram-bot ≥22, pydantic-settings, Typer, pytest + pytest-asyncio.

## Global Constraints

- Money is integer **cents** everywhere; only display/API-string conversion leaves cents.
- Gates (ALL must pass before each commit): `ruff check .` · `ruff format --check .` · `mypy --strict .` · `pytest`.
- `domain/splitwise_ledger.py` MUST hit 100% line+branch coverage (add to the `--cov` list in `pyproject.toml`).
- HTML parse mode everywhere; `callback_data` ≤ 64 bytes (numeric ids + short opcodes only).
- Keyboard-wizard first: new commands are argless and open wizards; free text only for the email.
- Secrets only in `.env` (`SPLITWISE_API_KEY`); everything else in `config.yaml`. Never commit secrets/DB.
- Migrations are append-only; never edit prior migrations. Current HEAD: `b2c3d4e5f6a7`.
- Feature is dormant unless `SPLITWISE_API_KEY` AND `splitwise_group_id` are both set.
- §11 maintenance rule: command/scoring/rule changes update `/ajuda` text AND `COMPLETION.md` in the same change.

---

### Task 1: Config — Splitwise settings + feature gate

**Files:**
- Modify: `tigrinho/config.py` (Settings class)
- Modify: `tests/test_config.py`
- Modify: `config.example.yaml`, `.env.example`

**Interfaces — Produces:**
- `Settings.splitwise_api_key: str | None`
- `Settings.splitwise_group_id: int | None`
- `Settings.splitwise_base_url: str` (default `https://secure.splitwise.com/api/v3.0`)
- `Settings.splitwise_currency_code: str` (default `BRL`)
- `Settings.splitwise_enabled: bool` (property) → `splitwise_api_key not None and splitwise_group_id not None`

- [ ] **Step 1:** Add fields: `splitwise_api_key: str | None = None` (in the secrets block), and in the config.yaml block `splitwise_group_id: int | None = None`, `splitwise_base_url: str = "https://secure.splitwise.com/api/v3.0"`, `splitwise_currency_code: str = "BRL"`. Add `@property def splitwise_enabled(self) -> bool: return self.splitwise_api_key is not None and self.splitwise_group_id is not None`.
- [ ] **Step 2:** Test `splitwise_enabled` is False by default and True when both set (extend test_config.py).
- [ ] **Step 3:** Add `splitwise_group_id`, `splitwise_base_url`, `splitwise_currency_code` to `config.example.yaml` (commented sample) and `SPLITWISE_API_KEY=` to `.env.example`.
- [ ] **Step 4:** Gates + commit `feat(config): splitwise settings + enabled gate`.

---

### Task 2: Enum + models — SplitwiseMode, Player + Tournament columns

**Files:**
- Modify: `tigrinho/enums.py` (add `SplitwiseMode`)
- Modify: `tigrinho/db/models.py` (Player, Tournament)
- Modify: `tests/test_models.py` (or test_repositories.py)

**Interfaces — Produces:**
- `class SplitwiseMode(enum.StrEnum): AUTO="AUTO"; MANUAL="MANUAL"; EXCLUDED="EXCLUDED"`
- `Player.splitwise_user_id: Mapped[int | None]` (BigInteger), `Player.splitwise_email: Mapped[str | None]`
- `Tournament.splitwise_mode: Mapped[SplitwiseMode]` (default MANUAL, server_default "MANUAL"), `splitwise_expense_id: Mapped[int | None]` (BigInteger), `splitwise_synced_signature: Mapped[str | None]`, `splitwise_admin_notified_at: Mapped[datetime | None]`

- [ ] **Step 1:** Add `SplitwiseMode` StrEnum to `tigrinho/enums.py` (mirror `TournamentStatus` style). Re-export via `models.py` if it follows the enums re-export pattern.
- [ ] **Step 2:** Add the Player columns (`mapped_column(BigInteger, default=None)` / `mapped_column(String, default=None)`).
- [ ] **Step 3:** Add the Tournament columns. `splitwise_mode: Mapped[SplitwiseMode] = mapped_column(Enum(SplitwiseMode, name="splitwise_mode"), default=SplitwiseMode.MANUAL, server_default=SplitwiseMode.MANUAL.value)`.
- [ ] **Step 4:** Test: with the `session` fixture, create a Player + Tournament, assert defaults (`splitwise_user_id is None`, `splitwise_mode == MANUAL`).
- [ ] **Step 5:** Gates + commit `feat(models): splitwise columns + SplitwiseMode enum`.

---

### Task 3: Migration — columns + transition data-fix

**Files:**
- Create: `tigrinho/db/migrations/versions/c3d4e5f6a7b8_add_splitwise.py` (revises `b2c3d4e5f6a7`)
- Create: `tests/test_migration_splitwise.py`

- [ ] **Step 1:** Write the migration. `upgrade()`:
```python
def upgrade() -> None:
    with op.batch_alter_table("players", schema=None) as b:
        b.add_column(sa.Column("splitwise_user_id", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("splitwise_email", sa.String(), nullable=True))
    with op.batch_alter_table("tournaments", schema=None) as b:
        b.add_column(sa.Column("splitwise_mode", sa.Enum("AUTO", "MANUAL", "EXCLUDED", name="splitwise_mode"), nullable=False, server_default="MANUAL"))
        b.add_column(sa.Column("splitwise_expense_id", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("splitwise_synced_signature", sa.String(), nullable=True))
        b.add_column(sa.Column("splitwise_admin_notified_at", sa.DateTime(), nullable=True))
    # Transition: existing closed bolãozinhos are out of scope (covers ones settled by hand).
    op.execute("UPDATE tournaments SET splitwise_mode = 'EXCLUDED' WHERE status IN ('FINISHED', 'CANCELLED')")
```
`downgrade()` drops the six columns (batch).
- [ ] **Step 2:** Test (`test_migration_splitwise.py`): build a raw pre-migration sqlite via running migrations up to `b2c3d4e5f6a7`, insert one OPEN + one FINISHED tournament row, run `upgrade()` to head, assert OPEN→MANUAL, FINISHED→EXCLUDED, and the player columns exist. Use `alembic.config.Config` + `command.upgrade`, or the project's existing migration-test helper if present; otherwise seed via raw SQL and call the module's `upgrade()` under an `op` context. (If a clean alembic-driven test is too heavy, assert the data-fix SQL via a direct sqlite exec on a seeded DB.)
- [ ] **Step 3:** Manually verify: `alembic upgrade head` on a scratch DB applies cleanly; `alembic downgrade -1` reverts.
- [ ] **Step 4:** Gates + commit `feat(db): migration for splitwise columns + transition data-fix`.

---

### Task 4: Pure ledger math — `domain/splitwise_ledger.py`

**Files:**
- Create: `tigrinho/domain/splitwise_ledger.py`
- Create: `tests/test_splitwise_ledger.py`
- Modify: `pyproject.toml` (add `--cov=tigrinho.domain.splitwise_ledger`)

**Interfaces — Produces:**
- `@dataclass(frozen=True, slots=True) class LedgerShare: paid_cents: int; owed_cents: int`
- `def build_ledger(entrant_ids: Sequence[int], winner_ids: Sequence[int], entry_price_cents: int) -> dict[int, LedgerShare]`
- `def ledger_cost_cents(ledger: Mapping[int, LedgerShare]) -> int`
- `def cents_to_amount(cents: int) -> str`  (2-decimal API string, no float: `divmod(cents, 100)` → `f"{w}.{r:02d}"`)

- [ ] **Step 1:** Write failing tests in `tests/test_splitwise_ledger.py`:
  - single winner: `build_ledger([1,2,3],[1],1000)` → loser 2,3 owe 1000/paid 0; winner 1 paid 2000/owed 0; cost 2000.
  - two-way tie odd cents: `build_ledger([1,2,3],[1,2],1001)` → losers {3} owe 1001; cost 1001; winners split 1001 → 501/500 (first winner gets the extra cent), each owed 0.
  - lone entrant: `build_ledger([1],[1],1000)` → `{}` (no losers → cost 0).
  - full tie (no losers): `build_ledger([1,2],[1,2],1000)` → `{}`.
  - balance invariant: for each case, `sum(paid) == sum(owed) == ledger_cost_cents`.
  - `cents_to_amount(9000) == "90.00"`, `cents_to_amount(501) == "5.01"`, `cents_to_amount(0) == "0.00"`.
- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3:** Implement. `build_ledger`: `losers = [i for i in entrant_ids if i not in set(winner_ids)]`; `cost = len(losers) * entry_price_cents`; if `cost == 0` return `{}`. Each loser → `LedgerShare(0, entry_price_cents)`. Winners (sorted asc): `base, rem = divmod(cost, k)`; winner j gets `paid = base + (1 if j < rem else 0)`, `owed=0`. Build dict (losers + winners).
- [ ] **Step 4:** Run, verify pass + 100% coverage on the module.
- [ ] **Step 5:** Add `--cov=tigrinho.domain.splitwise_ledger` to `addopts` in `pyproject.toml`.
- [ ] **Step 6:** Gates + commit `feat(domain): pure splitwise ledger math`.

---

### Task 5: Async Splitwise client — `providers/splitwise.py`

**Files:**
- Create: `tigrinho/providers/splitwise.py`
- Create: `tests/test_splitwise_client.py`

**Interfaces — Produces (mirror `ApiFootballProvider`):**
- `class SplitwiseError(RuntimeError)`
- `@dataclass(frozen=True, slots=True) class SplitwiseUser: id: int; email: str | None; first_name: str; last_name: str | None`
- `@dataclass(frozen=True, slots=True) class SplitwiseMember: id: int; email: str | None; first_name: str; last_name: str | None`
- `@dataclass(frozen=True, slots=True) class ExpenseShare: user_id: int; paid_cents: int; owed_cents: int`
- `class SplitwiseClient` with:
  - `__init__(self, *, base_url: str, api_key: str, timeout: float = 15.0, client: httpx.AsyncClient | None = None)` — `Authorization: Bearer <key>` header
  - `async def aclose(self) -> None`
  - `async def get_current_user(self) -> SplitwiseUser`  (`GET /get_current_user`)
  - `async def get_group_members(self, group_id: int) -> list[SplitwiseMember]`  (`GET /get_group/{id}`)
  - `async def add_user_to_group(self, group_id: int, *, email: str, first_name: str) -> SplitwiseUser`  (`POST /add_user_to_group`)
  - `async def create_expense(self, *, group_id: int, cost_cents: int, currency_code: str, description: str, shares: Sequence[ExpenseShare]) -> int`  (`POST /create_expense` → expense id)
  - `async def update_expense(self, expense_id: int, *, cost_cents: int, description: str, shares: Sequence[ExpenseShare]) -> None`  (`POST /update_expense/{id}`)

- [ ] **Step 1:** Write `tests/test_splitwise_client.py` using `httpx.MockTransport(handler)` (copy `_provider` style from `tests/test_api_football.py`):
  - `get_current_user` parses `{"user": {...}}`.
  - `get_group_members` parses `{"group": {"members": [...]}}`.
  - `create_expense` posts `cost`, `description`, `group_id`, `currency_code`, and `users__0__user_id/paid_share/owed_share` form fields; parses `{"expenses":[{"id":777}], "errors":{}}` → 777.
  - `create_expense` raises `SplitwiseError` when body has non-empty `errors` (HTTP 200 with `{"errors":{"base":["x"]}}`).
  - `update_expense` posts to `/update_expense/777`.
  - `add_user_to_group` returns the created `{"user":{...}}`.
- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3:** Implement: AsyncClient with base_url + bearer header (injectable `client`). Build form data with `data={"cost": cents_to_amount(cost_cents), "currency_code": ..., "description": ..., "group_id": ..., "users__0__user_id": ..., "users__0__paid_share": cents_to_amount(...), "users__0__owed_share": cents_to_amount(...), ...}` (POST form-encoded, per Splitwise API). After each call: `response.raise_for_status()`; parse JSON; if `body.get("errors")` truthy → `raise SplitwiseError(...)`. Doc URL comment: `# https://dev.splitwise.com/`.
- [ ] **Step 4:** Run, verify pass.
- [ ] **Step 5:** Gates + commit `feat(providers): async Splitwise client`.

---

### Task 6: pt-BR text + /ajuda

**Files:**
- Modify: `tigrinho/domain/text_pt.py`
- Modify: `tests/test_text_pt.py`

**Interfaces — Produces:**
- `splitwise_link_intro_text() -> str` ("Você já está no grupo do Splitwise?")
- `splitwise_ask_email_text() -> str`
- `splitwise_linked_text(*, member_name: str) -> str`
- `splitwise_invalid_email_text() -> str`
- `splitwise_link_required_text() -> str` (join-guard rejection)
- `splitwise_all_linked_text() -> str` ("todo mundo do grupo já está vinculado…")
- `splitwise_expense_description(*, name: str, winners: Sequence[str]) -> str` ("🏆 Bolãozinho '<name>' — <winner(s)>")
- `splitwise_admin_ready_text(*, tournament_id: int, name: str) -> str`
- updated `help_text()` adding `/vincular_splitwise` and `/bolaozinho_splitwise` + a short "Splitwise" paragraph.

- [ ] **Step 1:** Tests asserting each function returns the expected pt-BR substring; help_text contains `/vincular_splitwise` and `/bolaozinho_splitwise`.
- [ ] **Step 2:** Implement (HTML-escape any interpolated names via `escape`).
- [ ] **Step 3:** Gates + commit `feat(text): splitwise pt-BR strings + /ajuda` (§11).

---

### Task 7: Callbacks + keyboards (wizard)

**Files:**
- Modify: `tigrinho/bot/callbacks.py`
- Modify: `tigrinho/bot/keyboards.py`
- Modify: `tests/test_callbacks.py`, `tests/test_keyboards.py`

**Interfaces — Produces (new opcodes, ≤64 bytes):**
- `sv` — link wizard "Sim, já estou no grupo" → show member picker. `SplitwiseAction(op, value)` minimal.
- `sn` — "Não estou no grupo" → start email entry.
- `sp:<sw_user_id>` — pick a member (carries the Splitwise user id). `SplitwiseMemberPick(splitwise_user_id: int)`.
- `sr:<tournament_id>` — admin register-this-bolão pick. `SplitwiseRegisterPick(tournament_id: int)`.
- Keyboards: `splitwise_intro_keyboard()` (Sim `sv` / Não `sn`), `splitwise_member_keyboard(members: Sequence[tuple[int,str]])` (one `sp:<id>` button each), `splitwise_register_keyboard(items: Sequence[tuple[int,str]])` (one `sr:<id>` each), and a `splitwise_link_button()` (🔗) for the join card.

- [ ] **Step 1:** Tests: encode/decode round-trip for `sv`, `sn`, `sp:123456789`, `sr:42`; reject >64 bytes; keyboard builders produce the right callback_data.
- [ ] **Step 2:** Implement: extend the `decode`/`encode` match with the new dataclasses; add keyboard builders mirroring `tournament_*_keyboard`.
- [ ] **Step 3:** Gates + commit `feat(bot): splitwise wizard callbacks + keyboards`.

---

### Task 8: Service — mode logic, build_registration, guards

**Files:**
- Create: `tigrinho/splitwise_service.py`
- Modify: `tigrinho/tournament_service.py` (`open_tournament` AUTO stamp; `join` guard)
- Create: `tests/test_splitwise_service.py`; Modify: `tests/test_tournament_service.py`

**Interfaces — Produces:**
- `def initial_splitwise_mode(status: TournamentStatus) -> SplitwiseMode` (FINISHED/CANCELLED→EXCLUDED else MANUAL)
- `@dataclass(frozen=True, slots=True) class SplitwiseRegistration: tournament_id: int; expense_id: int | None; cost_cents: int; description: str; shares: tuple[ExpenseShare, ...]; signature: str; is_correction: bool`
- `def build_registration(session: Session, tournament_id: int) -> SplitwiseRegistration | None`
- `def mark_synced(tournament: Tournament, *, expense_id: int, signature: str) -> None`
- `def manual_ready_tournaments(session: Session) -> list[Tournament]` (FINISHED, mode MANUAL, expense_id None, all entrants linked, admin_notified_at None)
- `def auto_unsynced_tournaments(session: Session) -> list[Tournament]` (FINISHED, mode AUTO, signature mismatch)
- `def all_entrants_linked(session: Session, tournament_id: int) -> bool`

**Consumes:** `domain.splitwise_ledger.build_ledger`, `domain.tournament.compute_outcome`, `tournament_service.signature_of`, `providers.splitwise.ExpenseShare`, repos.

- [ ] **Step 1:** `open_tournament` — add param `splitwise_enabled: bool = False`; when True set `tournament.splitwise_mode = SplitwiseMode.AUTO`. Test both branches.
- [ ] **Step 2:** `join` — after `get_or_create`, fetch player; if `tournament.splitwise_mode is SplitwiseMode.AUTO and player.splitwise_user_id is None` → `raise TournamentError("Vincule seu Splitwise antes de entrar.")`. Test: AUTO+unlinked rejected; AUTO+linked ok; MANUAL+unlinked ok.
- [ ] **Step 3:** `build_registration` — load tournament; return None if mode EXCLUDED. Compute `outcome = compute_outcome(repo.standings(id), entry_price)`; return None if not `outcome.has_result`. `entrant_ids = repo.entry_ids(id)`; load each Player's `splitwise_user_id`; return None if any None. `ledger = build_ledger(entrant_ids, outcome.winner_ids, entry_price)`; return None if empty (cost 0). Map telegram→user_id → `shares`. `signature = signature_of(outcome)`; return None if `signature == tournament.splitwise_synced_signature`. `is_correction = tournament.splitwise_expense_id is not None`. Build description from winners' display names. Tests cover each None path + the create + the update(correction) path.
- [ ] **Step 4:** `manual_ready_tournaments`, `auto_unsynced_tournaments`, `all_entrants_linked`, `mark_synced`, `initial_splitwise_mode` + tests.
- [ ] **Step 5:** Gates + commit `feat(service): splitwise registration logic + join guard + AUTO stamp`.

---

### Task 9: Linking wizard handlers + join-card button + deep link

**Files:**
- Create: `tigrinho/bot/splitwise_handlers.py`
- Modify: `tigrinho/bot/tournament_handlers.py` (🔗 on AUTO join card; surface guard rejection with the 🔗 button)
- Modify: `tigrinho/bot/bets_handlers.py` (`start_handler`: `vincular` deep-link payload)
- Modify: `tigrinho/bot/app.py` (register splitwise handlers BEFORE bet wizard)
- Create: `tests/test_splitwise_handlers.py`

**Interfaces — Produces:**
- `cmd_vincular_splitwise(update, context)` — argless; DM → show intro (`splitwise_intro_keyboard`); group → deep-link button `?start=vincular`.
- `on_splitwise_callback(update, context)` — dispatch `sv`/`sn`/`sp`; registered with `CallbackQueryHandler(pattern="^(sv|sn|sp|sr):")`.
- `on_splitwise_email_text(update, context)` — `MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND)` guarded by `context.user_data["awaiting_splitwise_email"]`.
- `register_splitwise_handlers(application)`.

- [ ] **Step 1:** `cmd_vincular_splitwise`: feature-disabled → reply "Splitwise não está configurado." Else DM → fetch group members via the client (async), filter out members whose `id` is already a `Player.splitwise_user_id`, show intro keyboard. Group → deep-link button.
- [ ] **Step 2:** `sv` (Sim) → show `splitwise_member_keyboard(unlinked_members)` (or `splitwise_all_linked_text` + offer `sn` if none). `sp:<id>` → store `player.splitwise_user_id` + email, confirm. `sn` (Não) → set `context.user_data["awaiting_splitwise_email"]=True`, prompt for email.
- [ ] **Step 3:** `on_splitwise_email_text`: if flag unset → return. Validate email (simple regex). On invalid → `splitwise_invalid_email_text`, keep flag. On valid → `add_user_to_group` (async, best-effort) → store returned `user_id` + email → confirm; clear flag.
- [ ] **Step 4:** Join card: in `tournament_handlers._render_join_card`, when the tournament is AUTO and the actor is unlinked, add the 🔗 link button. When `join` raises the link `TournamentError`, the toast/message includes the 🔗 button. (Pass actor linkage in.)
- [ ] **Step 5:** `start_handler`: add `if payload == "vincular": await start the link wizard`.
- [ ] **Step 6:** Register handlers (CommandHandler `vincular_splitwise`, the CallbackQueryHandler, the guarded MessageHandler) in `register_splitwise_handlers`, called from `build_application` before `register_bet_handlers`.
- [ ] **Step 7:** Tests with `AsyncMock` bot: intro shown; `sp` stores user_id; Não→email→add_user_to_group→stored; invalid email re-prompts; guard rejection shows 🔗. Use a fake/mocked SplitwiseClient (inject via app_context — see Task 14).
- [ ] **Step 8:** Gates + commit `feat(bot): splitwise linking wizard + join guard surfacing`.

---

### Task 10: AUTO auto-registration from `resolve_and_post`

**Files:**
- Modify: `tigrinho/bot/runtime.py` (AppContext: `splitwise_client: SplitwiseClient | None = None`, `splitwise_corrections: dict[int,int] = field(default_factory=dict)`)
- Modify: `tigrinho/bot/tournament_announce.py` (after winner posts, register AUTO)
- Create: `tigrinho/bot/splitwise_register.py` (the async execute helper)
- Create: `tests/test_splitwise_register.py`

**Interfaces — Produces:**
- `async def register_finished_tournament(app_context, context, tournament_id: int, *, is_correction: bool) -> None` — gate on `settings.splitwise_enabled` and `splitwise_client`; load tournament; only if `mode is AUTO`; `build_registration`; if None → return; on correction cap via `app_context.splitwise_corrections` (cap 2, mirror `CORRECTION_POST_CAP`); call `create_expense`/`update_expense`; `mark_synced` + commit; best-effort (SplitwiseError/httpx → log + `notify_admin`).

- [ ] **Step 1:** Add AppContext fields.
- [ ] **Step 2:** Implement `register_finished_tournament`.
- [ ] **Step 3:** In `tournament_announce`, after a `TournamentWinnerAnnouncement` is posted, call `register_finished_tournament(..., is_correction=ann.is_correction)`.
- [ ] **Step 4:** Tests: AUTO finished → create called + expense_id persisted; correction → update called; cap respected; disabled → no call; MANUAL → no call; SplitwiseError → admin DM, no crash.
- [ ] **Step 5:** Gates + commit `feat(bot): auto-register AUTO bolãozinhos in Splitwise at settle`.

---

### Task 11: Sweep — AUTO retry + MANUAL ready-notify

**Files:**
- Modify: `tigrinho/bot/sweep_job.py`
- Modify: `tests/test_sweep_job.py`

- [ ] **Step 1:** In `_run_sweep`, when `settings.splitwise_enabled`: for `auto_unsynced_tournaments` → `register_finished_tournament(..., is_correction=True_if_expense_exists)` (retry). For `manual_ready_tournaments` → `notify_admin(splitwise_admin_ready_text(...))` once, set `splitwise_admin_notified_at`, commit.
- [ ] **Step 2:** Tests: a stuck AUTO retries; a MANUAL that became fully-linked DMs admin once and never again; disabled → neither.
- [ ] **Step 3:** Gates + commit `feat(bot): sweep retries AUTO + notifies admin for ready MANUAL bolãozinhos`.

---

### Task 12: Admin manual-trigger wizard `/bolaozinho_splitwise`

**Files:**
- Modify: `tigrinho/bot/splitwise_handlers.py` (cmd + `sr` handler)
- Modify: `tigrinho/bot/app.py` (PRIVATE_COMMANDS, register cmd)
- Modify: `tests/test_splitwise_handlers.py`

- [ ] **Step 1:** `cmd_bolaozinho_splitwise` (admin-only via `app_context.settings.admin_user_id`): argless → picker (`splitwise_register_keyboard`) of `manual_ready_tournaments`. Empty → "Nenhum bolãozinho pronto."
- [ ] **Step 2:** `sr:<id>` → `build_registration` + `create_expense` + `mark_synced` + commit; confirm to admin. Best-effort errors → admin message.
- [ ] **Step 3:** Add `/bolaozinho_splitwise` to PRIVATE_COMMANDS + register CommandHandler.
- [ ] **Step 4:** Tests: non-admin rejected; picker lists ready ones; `sr` registers + confirms.
- [ ] **Step 5:** Gates + commit `feat(bot): admin /bolaozinho_splitwise manual register wizard`.

---

### Task 13: CLI tools

**Files:**
- Modify: `tigrinho/cli.py` (bolaozinho sub-app)
- Modify: `tests/test_cli.py`

- [ ] **Step 1:** `splitwise-status [id]` — read-only: per bolão print mode, expense_id, linked/total entrants.
- [ ] **Step 2:** `splitwise-exclude <id> [--yes]` — set `splitwise_mode = EXCLUDED`.
- [ ] **Step 3:** `register-splitwise <id> [--force] [--yes]` — build the registration (with `--force`: drop unlinked losers, refuse if any winner unlinked) and call Splitwise via `asyncio.run`. Build a `SplitwiseClient` from settings.
- [ ] **Step 4:** `nudge-splitwise [--yes]` — for OPEN non-EXCLUDED bolãozinhos, DM unlinked entrants the 🔗 prompt via `asyncio.run` + `telegram.Bot(token)` (best-effort). Logs reached/total.
- [ ] **Step 5:** Tests: status output; exclude flips mode; register calls a mocked client; nudge iterates unlinked. (Mock the async client / Bot.)
- [ ] **Step 6:** Gates + commit `feat(cli): bolaozinho splitwise status/exclude/register/nudge`.

---

### Task 14: Startup wiring + validation

**Files:**
- Modify: `tigrinho/bot/runtime.py` or wherever AppContext is assembled (the bot entrypoint, e.g. `tigrinho/__main__.py` / `bot/app.py`)
- Modify: `tigrinho/bot/app.py` (`validate_startup`/`post_init`)
- Modify: `tests/test_app.py` (or wherever startup is tested)

- [ ] **Step 1:** Build `SplitwiseClient` when `settings.splitwise_enabled` and inject into `AppContext.splitwise_client`; else `None`. (Find the AppContext construction site — same place provider/budget are built.)
- [ ] **Step 2:** In `validate_startup`, when enabled, `await client.get_current_user()`; on failure log a clear error + admin DM and proceed (feature non-critical — do not crash the bot). The CLI builds its own client on demand.
- [ ] **Step 3:** Tests: enabled wiring builds a client; disabled → None; validation failure does not raise.
- [ ] **Step 4:** Gates + commit `feat(bot): wire + validate Splitwise client at startup`.

---

### Task 15: Docs (COMPLETION.md §23, README, PROGRESS, CLAUDE.local.md)

**Files:**
- Modify: `COMPLETION.md` (new §23 + config table rows + secret note + change-log; note tie-vs-prize divergence)
- Modify: `README.md`
- Modify: `PROGRESS.md` (Feature 8 milestone + ticks)
- Modify: `CLAUDE.local.md` (4th secret `SPLITWISE_API_KEY`; `splitwise_group_id`/`splitwise_currency_code` to scp)

- [ ] **Step 1:** Write §23 from the spec (modes, ledger, linking wizard, transition, commands, config). Add `splitwise_group_id`, `splitwise_base_url`, `splitwise_currency_code` to the config table and the `SPLITWISE_API_KEY` secret note; add a change-log entry.
- [ ] **Step 2:** README + PROGRESS + CLAUDE.local.md updates.
- [ ] **Step 3:** Gates (docs don't affect them) + commit `docs: COMPLETION.md §23 + README/PROGRESS/CLAUDE.local for Splitwise` (§11).

---

### Task 16: Full-suite green + integration smoke

- [ ] **Step 1:** Run all four gates from the repo root; fix anything red.
- [ ] **Step 2:** Smoke: `alembic upgrade head` on a scratch DB; construct `Settings` with splitwise enabled + a `FakeSplitwiseClient` and run an AUTO finish end-to-end in a test asserting `create_expense` was called with a balanced ledger.
- [ ] **Step 3:** Commit any fixups. Branch ready to merge.

---

## Self-Review

- **Spec coverage:** config (T1), data model+migration+transition (T2,T3), ledger (T4), client (T5), text/ajuda (T6), wizard callbacks (T7), service+guard+AUTO stamp (T8), linking wizard (T9), AUTO register (T10), sweep retry+notify (T11), admin manual wizard (T12), CLI incl. --force/exclude/nudge/status (T13), startup validation (T14), docs (T15), smoke (T16). All §-sections of the spec map to a task.
- **Placeholders:** none — each task lists exact files, signatures, and the novel code.
- **Type consistency:** `ExpenseShare`/`LedgerShare`/`SplitwiseMode`/`SplitwiseRegistration` names are used identically across T4/T5/T8/T10/T13. `splitwise_enabled` property, `splitwise_client`/`splitwise_corrections` AppContext fields, and the `sv|sn|sp|sr` opcodes are consistent across T1/T7/T9/T10/T14.
