# Orthogonal Bet Categories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the coupled 5-market bet set with two orthogonal markets (`EXACT_SCORE` + new `HALF_TIME_RESULT`), rolled out per-game so only games with **no bets yet** (and all future games) use the new set, while every already-bet game keeps grading its old categories.

**Architecture:** `BetCategory` is **append-only** — the four removed markets (`WINNER`, `FIRST_TEAM`, `BTTS`, `OVER_UNDER`) keep their payloads/grading/codecs so historical bets still settle; they are merely dropped from the *offerable* set. A per-game `category_set` column (`legacy` | `v2`) decides which categories are *offered*; it is backfilled on migration: any game with ≥1 existing bet → `legacy`, everything else (and the column default) → `v2`. `HALF_TIME_RESULT` grades on the half-time score, newly plumbed from API-Football `score.halftime` → `MatchResult` → `GradingContext`, persisted on `games`.

**Tech Stack:** Python 3.12, python-telegram-bot 21.x, SQLAlchemy 2.0 + Alembic, httpx, pydantic, Typer, pytest. Mid-tournament live deploy (WC 2026).

## Global Constraints

- All four gates MUST pass before every commit: `ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest`.
- `domain/scoring.py` + `domain/settlement.py` stay **pure** (no I/O/clock/DB) and hold ~100% line+branch coverage.
- No `Any` in domain; prefer `Enum`, frozen dataclasses, exhaustive `match`/`assert_never`.
- Migrations are **append-only**; new migration's `down_revision = "d4e5f6a7b8c9"` (current head).
- Inline `callback_data` ≤ 64 bytes; HTML parse mode; keyboard-wizard-first for Telegram commands.
- Maintenance rule: any bet-category/scoring/grading change updates `/ajuda` (`help_text`) **and** `COMPLETION.md` in the same change.
- Append-only invariant: a settlement test MUST prove an old-category (e.g. `WINNER`) bet still grades after the change.

## pt-BR copy (verbatim)

- `HALF_TIME_RESULT` label: `"Quem está na frente no 1º tempo"`; buttons: real home/away names + `"Empate"`.
- Wizard prompt: `"⏱ Quem está na frente no intervalo (1º tempo)?"`
- `describe_bet` prefix: `"1º tempo"`; value: home name / `"Empate"` / away name.

---

### Task 1: Domain bet type — `HalfTimeResult` + `CategorySet`/offerable

**Files:**
- Modify: `tigrinho/enums.py` (add `CategorySet`)
- Modify: `tigrinho/domain/bets.py`
- Test: `tests/test_bets.py`

**Interfaces produced:**
- `CategorySet(StrEnum){LEGACY="legacy", V2="v2"}` in `tigrinho.enums`.
- `HalfTimeSel(StrEnum){HOME,DRAW,AWAY}`, `HalfTimeResultPayload{sel: HalfTimeSel}` (CATEGORY=`HALF_TIME_RESULT`).
- `BetCategory.HALF_TIME_RESULT = "HALF_TIME_RESULT"` appended (after OVER_UNDER, append-only).
- `Payload` union gains `HalfTimeResultPayload`; `parse_payload`/`serialize_payload` handle it.
- `OFFERABLE: dict[CategorySet, tuple[BetCategory, ...]]` and `offerable_for(category_set) -> tuple[BetCategory, ...]` in `domain/bets.py`:
  - `LEGACY → (EXACT_SCORE, FIRST_TEAM, BTTS, WINNER, OVER_UNDER)`
  - `V2 → (EXACT_SCORE, HALF_TIME_RESULT)`

- [ ] **Step 1: Write failing tests** — round-trip `HalfTimeResultPayload` through `serialize_payload`/`parse_payload`; assert `offerable_for(CategorySet.V2) == (BetCategory.EXACT_SCORE, BetCategory.HALF_TIME_RESULT)` and `offerable_for(CategorySet.LEGACY)` has the 5 old ones; assert `BetCategory.HALF_TIME_RESULT` exists.
- [ ] **Step 2: Run** `pytest tests/test_bets.py -q` → FAIL.
- [ ] **Step 3: Implement.** In `enums.py` add `CategorySet`. In `bets.py`: append enum member; add `HalfTimeSel`, `HalfTimeResultPayload`; extend `Payload`, `parse_payload` (`case BetCategory.HALF_TIME_RESULT: return HalfTimeResultPayload.model_validate_json(...)`); add `OFFERABLE` + `offerable_for`.
- [ ] **Step 4: Run** `pytest tests/test_bets.py -q` → PASS; `mypy --strict tigrinho/domain/bets.py tigrinho/enums.py`.
- [ ] **Step 5: Commit** `feat(bets): add HALF_TIME_RESULT category + per-regime offerable set (§8.1)`.

### Task 2: Scoring — points + grading branch

**Files:** Modify `tigrinho/domain/scoring.py`; Test `tests/test_scoring.py`.

**Interfaces produced:**
- `POINTS[BetCategory.HALF_TIME_RESULT] = 2`.
- `GradingContext` gains `home_goals_ht: int | None = None`, `away_goals_ht: int | None = None` (keep frozen+slots; defaulted so legacy construction is unaffected).
- `is_correct(HalfTimeResultPayload, ctx)`: `False` if either HT field is `None` (void, like FIRST_TEAM on 0-0); else `HOME` if `home_ht>away_ht`, `AWAY` if `away_ht>home_ht`, else `DRAW`; compare to `payload.sel`.

- [ ] **Step 1: Write failing tests** (table-driven): HOME lead (2-1 HT→HOME correct, DRAW/AWAY wrong), AWAY lead, level HT→DRAW correct, **missing HT** (`home_goals_ht=None`) → `is_correct` False for every `HalfTimeSel`; `POINTS[HALF_TIME_RESULT]==2`; `grade()` awards 2 when correct, 0 when wrong/void.
- [ ] **Step 2: Run** `pytest tests/test_scoring.py -q` → FAIL.
- [ ] **Step 3: Implement** the `POINTS` entry, the two optional fields, a private `_half_time_outcome(ctx) -> HalfTimeSel | None` (None only when a field is None), and the `isinstance(payload, HalfTimeResultPayload)` branch in `is_correct`.
- [ ] **Step 4: Run** `pytest tests/test_scoring.py -q --cov=tigrinho/domain/scoring --cov-branch` → PASS, branch coverage on the new branch.
- [ ] **Step 5: Commit** `feat(scoring): grade HALF_TIME_RESULT on the half-time score (§8.1)`.

### Task 3: Settlement domain — thread HT + consistency guard

**Files:** Modify `tigrinho/domain/settlement.py`; Test `tests/test_settlement.py`.

**Interfaces produced:** `build_context` populates `home_goals_ht`/`away_goals_ht` from `MatchResult`; raises `ValueError` only when an HT field is present **and** exceeds its 90′ counterpart (corrupt data). The existing missing-90′ `ValueError` stays. No raise when HT is `None`.

- [ ] **Step 1: Write failing tests** — `build_context` copies HT through to the context; `home_goals_ht=3` with `home_goals_90=1` raises `ValueError`; an old-category (`WINNER`) bet still grades correctly through `settle_game` (append-only invariant); a `HALF_TIME_RESULT` bet with `home_goals_ht=None` settles to `points=0` (no raise).
- [ ] **Step 2: Run** `pytest tests/test_settlement.py -q` → FAIL.
- [ ] **Step 3: Implement** the HT passthrough + per-side guard in `build_context`.
- [ ] **Step 4: Run** `pytest tests/test_settlement.py -q --cov=tigrinho/domain/settlement --cov-branch` → PASS.
- [ ] **Step 5: Commit** `feat(settlement): thread half-time score + corrupt-data guard (§8.3)`.

### Task 4: Provider — `MatchResult` HT fields + mapper

**Files:** Modify `tigrinho/providers/base.py`, `tigrinho/providers/api_football.py`; Test `tests/test_api_football.py`.

**Interfaces produced:** `MatchResult.home_goals_ht: int | None = None`, `away_goals_ht: int | None = None`. `map_match_result` parses `item["score"]["halftime"]` (`_opt_int`).

- [ ] **Step 1: Write failing test** — `map_match_result` with a `score.halftime={"home":1,"away":0}` populates `home_goals_ht=1/away_goals_ht=0`; missing/`null` halftime → both `None`.
- [ ] **Step 2: Run** `pytest tests/test_api_football.py -q` → FAIL.
- [ ] **Step 3: Implement** the two fields + `halftime = (item.get("score") or {}).get("halftime") or {}` parse.
- [ ] **Step 4: Run** `pytest tests/test_api_football.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(provider): parse score.halftime into MatchResult`.

### Task 5: DB model + migration — HT columns + `category_set` backfill

**Files:** Modify `tigrinho/db/models.py`; Create `tigrinho/db/migrations/versions/<rev>_add_ht_score_and_category_set.py`; Test `tests/test_migrations.py` (or extend existing model/migration test).

**Interfaces produced:** `Game.home_goals_ht: Mapped[int|None]`, `Game.away_goals_ht: Mapped[int|None]`, `Game.category_set: Mapped[CategorySet]` (default `V2`, server_default `"v2"`).

Migration `upgrade()` (mirror the splitwise-enum add for the Enum column; verify `create_constraint`/`native_enum` against `c3d4e5f6a7b8_add_splitwise.py`):
```python
def upgrade() -> None:
    with op.batch_alter_table("games", schema=None) as batch_op:
        batch_op.add_column(sa.Column("home_goals_ht", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("away_goals_ht", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            "category_set", sa.String(), nullable=False, server_default="v2"))
    # Backfill: any game that already has ≥1 bet keeps the OLD set; everything else → v2 (default).
    op.execute(
        "UPDATE games SET category_set='legacy' "
        "WHERE fixture_id IN (SELECT DISTINCT fixture_id FROM bets)"
    )
```
`down_revision = "d4e5f6a7b8c9"`. `downgrade()` drops the three columns.

- [ ] **Step 1: Write failing test** — an in-memory/temp-file DB upgraded to head has a game with a bet → `category_set=='legacy'`, a game without bets → `'v2'`; HT columns nullable. (Reuse the existing migration test harness if present; else a small SQLite round-trip.)
- [ ] **Step 2: Run** the test → FAIL.
- [ ] **Step 3: Implement** the model columns + migration.
- [ ] **Step 4: Run** the test + `mypy --strict tigrinho/db/models.py` → PASS.
- [ ] **Step 5: Commit** `feat(db): half-time columns + per-game category_set with no-bets backfill`.

### Task 6: settlement_service — persist HT

**Files:** Modify `tigrinho/settlement_service.py`; Test `tests/test_settlement_service.py`.

- [ ] **Step 1: Failing test** — after `settle_fixture` with a result carrying `home_goals_ht/away_goals_ht`, the `Game` row has them persisted.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `game.home_goals_ht = result.home_goals_ht` / `game.away_goals_ht = result.away_goals_ht` next to the 90′ writes.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(settlement-service): persist half-time score on settle`.

### Task 7: callbacks — `HalfTimeResultInput` codec

**Files:** Modify `tigrinho/bot/callbacks.py`; Test `tests/test_callbacks.py`.

**Interfaces produced:** `HalfTimeResultInput{fixture_id:int, sel:HalfTimeSel}`; opcode `h:<fixture>:<H|D|A>`; `_CATEGORY_TO_CODE[HALF_TIME_RESULT]="HT"` (2-char ok, distinct from single-letter codes); `_HALF_TIME_TO_CODE`/`_CODE_TO_HALF_TIME` both directions; added to `CallbackData` union, `encode` (`case HalfTimeResultInput`), `decode` (`if op == "h"`).

- [ ] **Step 1: Failing tests** — `decode(encode(HalfTimeResultInput(1001, HalfTimeSel.DRAW))) == ...`; `ChooseCategory(1001, HALF_TIME_RESULT)` round-trips; all ≤64 bytes.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** dataclass, dicts, union/encode/decode arms, category code. **Note:** opcode `h` is unused today (verify no collision with existing single-letter ops — current ops: g,c,s,e,w,t,o,f,x,q,bv,gb,pjt,pjc,pv,mh,mg,mm,bg,sv,sn,sp,sr,bc,ba.. — `h` is free).
- [ ] **Step 4: Run** → PASS; `mypy --strict tigrinho/bot/callbacks.py`.
- [ ] **Step 5: Commit** `feat(callbacks): HALF_TIME_RESULT selector codec`.

### Task 8: keyboards — selector + regime-aware category picker

**Files:** Modify `tigrinho/bot/keyboards.py`; Test `tests/test_keyboards.py`.

**Interfaces produced:**
- `half_time_keyboard(fixture_id, home_team, away_team) -> InlineKeyboardMarkup` — three rows: home name (`HOME`), `"Empate"` (`DRAW`), away name (`AWAY`); **DRAW always shown** (no knockout hiding).
- `category_keyboard(fixture_id, categories: Sequence[BetCategory]) -> InlineKeyboardMarkup` — iterate the **passed** categories (not module `CATEGORY_ORDER`) + Cancel.

- [ ] **Step 1: Failing tests** — `category_keyboard(1001, offerable_for(CategorySet.V2))` decodes to exactly `[EXACT_SCORE, HALF_TIME_RESULT]`; `half_time_keyboard` yields 3 `HalfTimeResultInput`s incl. DRAW. Update `test_category_keyboard_has_five_categories` → split into legacy(5)/v2(2) cases.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Add `half_time_keyboard`; change `category_keyboard` signature to take `categories`; import `HalfTimeResultInput`, `HalfTimeSel`.
- [ ] **Step 4: Run** `pytest tests/test_keyboards.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(keyboards): half-time selector + regime-aware category picker`.

### Task 9: bets_handlers — wizard dispatch (match/case) + regime

**Files:** Modify `tigrinho/bot/bets_handlers.py`; Test `tests/test_bets_handlers.py`.

**Changes:**
- Import `HalfTimeResultInput` (callbacks), `half_time_keyboard` (keyboards), `HalfTimeResultPayload`, `offerable_for` (bets); `CategorySet` is read off `game.category_set`.
- Replace every `category_keyboard(fixture_id)` call (lines ~238, 312, 394) with `category_keyboard(fixture_id, offerable_for(game.category_set))` — `game` is in scope at each (`_enter_wizard`, `_step_category`, `_finalize`). At `_enter_wizard`/`_finalize` the keyboard is built after `session.commit()`; capture `offerable = offerable_for(game.category_set)` while the row is live.
- `_step_payload`: convert the `if/elif/.../else: # FIRST_TEAM` chain into `match category:` with one `case` per `BetCategory` + `assert_never(category)`; add `case BetCategory.HALF_TIME_RESULT:` → `half_time_keyboard(fixture_id, game.home_team_name, game.away_team_name)` with prompt `"⏱ Quem está na frente no intervalo (1º tempo)?"`.
- `on_callback`: add `case HalfTimeResultInput(fixture_id, sel): await _finalize(..., HalfTimeResultPayload(sel=sel))`.

- [ ] **Step 1: Failing tests** — dispatching `ChooseCategory(fixture, HALF_TIME_RESULT)` on a v2 game shows the half-time keyboard; `HalfTimeResultInput` finalizes a stored bet; category picker on a v2 game offers 2 categories. (Follow existing `test_bets_handlers.py` harness patterns.)
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_bets_handlers.py -q` + `mypy --strict tigrinho/bot/bets_handlers.py` → PASS.
- [ ] **Step 5: Commit** `feat(wizard): HALF_TIME_RESULT step + regime-aware category picker`.

### Task 10: text_pt — labels, order, describe, points table, /ajuda, denominator

**Files:** Modify `tigrinho/domain/text_pt.py`; Test `tests/test_text_pt.py`.

**Changes:**
- `CATEGORY_LABELS[HALF_TIME_RESULT] = "Quem está na frente no 1º tempo"`.
- `CATEGORY_ORDER` (full, all 6, render/reveal order): insert `HALF_TIME_RESULT` after `EXACT_SCORE`.
- `describe_bet_value`: add `isinstance(payload, HalfTimeResultPayload)` branch → `HOME→home_team / DRAW→"Empate" / AWAY→away_team` (same shape as Winner).
- `describe_bet`: add `elif isinstance(payload, HalfTimeResultPayload): prefix = "1º tempo"`.
- `points_table_text()`: iterate **the new offerable set** `offerable_for(CategorySet.V2)` (so `/ajuda` shows only `EXACT_SCORE 5` + `HALF_TIME_RESULT 2`), not all 6.
- `help_text()`: rewrite the `<b>Categorias de aposta</b>` block to the 2 new markets + the HT grading rule line; drop the FIRST_TEAM/BTTS/OVER_UNDER/WINNER bullet lines and the knockout-no-draw + first-team rule lines (those rules no longer apply to offered bets — keep a short note that older in-progress games may still show the previous categories).
- Per-game denominator: change `_bettors_line(bettors, total)` to take the per-game total; `reminder_text` items gain a trailing `total_categories: int`; replace module `TOTAL_CATEGORIES` use. Keep `TOTAL_CATEGORIES` defined (still referenced by PROGRESS docs) but stop using it in `_bettors_line`.

- [ ] **Step 1: Failing tests** — `describe_bet(HalfTimeResultPayload(sel=DRAW))` → `"1º tempo: Empate"`; `points_table_text()` contains `HALF_TIME_RESULT` label + `5`/`2` and **not** "Ambas marcam"; `_bettors_line` with `total=2` renders `(1/2)`; `help_text()` mentions "1º tempo" and not "Mais/Menos 2.5".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_text_pt.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(text): HALF_TIME_RESULT copy, regime points table, per-game denominator, /ajuda (§11)`.

### Task 11: reminder_job — per-game denominator

**Files:** Modify `tigrinho/bot/reminder_job.py`; Test `tests/test_reminder_job.py`.

**Changes:** `_GameView` gains `total_categories: int`; `_view` computes `len(offerable_for(game.category_set))`; the `reminder_text` call passes it per game; docstrings updated from "5 categories".

- [ ] **Step 1: Failing test** — a v2 game in the reminder renders `(n/2)` not `(n/5)`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_reminder_job.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(reminder): per-game category denominator`.

### Task 12: AI /palpite — regime-aware

**Files:** Modify `tigrinho/ai/schemas.py`, `tigrinho/ai/prompt.py`, `tigrinho/palpite_service.py`, `tigrinho/bot/palpite_handlers.py`; Test `tests/test_ai_schemas.py` (+ palpite tests).

**Changes:**
- `GamePalpite` gains `half_time_result: HalfTimeSel`; `payloads(categories: Sequence[BetCategory] | None = None)` returns only `categories` (in their order) when given, else all (back-compat). Map `HALF_TIME_RESULT → HalfTimeResultPayload(sel=self.half_time_result)`.
- `prompt._SYSTEM_INSTRUCTION`: add `half_time_result` to the JSON template + a grading-rule line (`"half_time_result: quem está na frente no fim do 1º tempo (HOME/DRAW/AWAY)"`).
- Thread regime to render: `RenderablePalpite`/`load_today_palpites` carry `category_set`; `palpite_handlers` renders `palpite.payloads(offerable_for(rp.category_set))`. (Requires `list_palpite_games` rows to expose `category_set` — the `Game` ORM already has it; read it in the service.)

- [ ] **Step 1: Failing tests** — `GamePalpite(...).payloads(offerable_for(CategorySet.V2))` returns exactly `[ExactScorePayload, HalfTimeResultPayload]`; schema accepts `half_time_result`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_ai_schemas.py -q` + mypy on touched files → PASS.
- [ ] **Step 5: Commit** `feat(palpite): predict HALF_TIME_RESULT, render per game regime (§20)`.

### Task 13: CLI set-result — `--ht-home/--ht-away`

**Files:** Modify `tigrinho/cli.py`; Test `tests/test_cli.py`.

**Changes:** add `ht_home`/`ht_away` `typer.Option(None)`; pass into `MatchResult(home_goals_ht=ht_home, away_goals_ht=ht_away)`. Echo a note when a `HALF_TIME_RESULT` bet exists but HT was omitted (those bets void).

- [ ] **Step 1: Failing test** — `set-result ... --ht-home 1 --ht-away 0` persists HT and grades a `HALF_TIME_RESULT` bet HOME-correct.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_cli.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(cli): set-result --ht-home/--ht-away for HALF_TIME_RESULT re-grade`.

### Task 14: Docs — COMPLETION.md, README, PROGRESS.md

**Files:** Modify `COMPLETION.md` (§8.1 table/grading/points/decision record, §8.2 picker, §9.3 denominator), `README.md` (categories line), `PROGRESS.md` (note the change), `docs/superpowers/specs/2026-06-20-...md` (mark rollout = per-game `category_set`, superseding the config cutoff).

- [ ] **Step 1:** Edit docs to describe the 2 offered markets + append-only legacy grading + per-game rollout + 5/2 points + base-rate note.
- [ ] **Step 2: Commit** `docs: bet-categories §8.1/§8.2/§9.3 + README + PROGRESS (maintenance rule)`.

### Task 15: Full-suite gate + append-only proof + cleanup

**Files:** any test files still red (`tests/test_keyboards.py::test_category_keyboard_has_five_categories`, etc.).

- [ ] **Step 1:** Run `ruff check . && ruff format --check . && mypy --strict . && pytest` → all green.
- [ ] **Step 2:** Confirm a `WINNER`/`BTTS` bet on a legacy game still grades (append-only invariant test from Task 3 passes).
- [ ] **Step 3: Commit** any remaining test fixes `test: align suite with 2-market offered set`.

---

## Self-review

- **Spec coverage:** domain bet (T1) · grading (T2) · settlement+guard (T3) · provider HT (T4) · persistence+migration+backfill (T5/T6) · callbacks (T7) · keyboards (T8) · wizard incl. `else:#FIRST_TEAM` fix (T9) · text/labels/order/describe/points/`/ajuda`/denominator (T10/T11) · `/palpite` (T12) · CLI `--ht` (T13) · docs (T14) · append-only proof + gates (T3/T15). Rollout = per-game `category_set` backfilled by "no bets" (T5), superseding the spec's config cutoff per the user's instruction.
- **Type consistency:** `offerable_for(category_set: CategorySet)` everywhere; `HalfTimeSel`/`HalfTimeResultPayload` names stable across callbacks/keyboards/handlers/text/ai; `category_keyboard(fixture_id, categories)` new arity updated at all 3 call sites + tests.
- **Deploy (post-merge):** merge → push `main` → `ssh bbdo` pull+rebuild (migration auto-runs the backfill) → read-only verify the `legacy`/`v2` split on prod.
