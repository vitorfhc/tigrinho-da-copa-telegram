# Bolãozinhos (Tournaments) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-money "bolãozinho" side-competitions over a fixed set of fixtures: anyone creates one, players `/entrar`, and when all member games resolve the bot announces the winner(s) and the prize (pot minus one entry, split on ties).

**Architecture:** A pure `domain/tournament.py` (pot/prize/winner math, 100%-covered) + 3 new ORM tables + a `TournamentRepository` + a Telegram-agnostic `tournament_service` whose single `on_game_resolved(session, fixture_id)` is invoked from every game state-change path (poll settle, reconcile re-grade, sync void, sync un-void) and a new `bolaozinho_sweep` job. Bot handlers drive identity-based inline pickers; the 1h reminder is augmented with capped entrant mentions.

**Tech Stack:** Python 3.12, python-telegram-bot 22.x, SQLAlchemy 2.0 (sync) + Alembic, pydantic-settings, Typer, structlog, pytest.

## Global Constraints (copied from the spec / CLAUDE.md — apply to every task)

- **Money is integer cents.** Never a float. Display formatting only at the `text_pt` layer.
- **Prize = pot − one entry** = `(n_entrants − 1) × entry_price_cents`; ties split it `prize ÷ k`; remainder shown as "sobra"; lone entrant prize = 0.
- **Uniform price:** entry price **freezes at the first entry**; `pot = count(entries) × entry_price_cents`.
- **Join closes at the first game's kickoff** (persisted one-way `locked_at`); games/price also freeze then (price additionally freezes at first entry).
- **Permissions:** anyone creates/reads/joins; only `created_by` creator **or** `admin_user_id` may manage a given bolãozinho.
- **User-facing word = "bolãozinho"**, commands `/bolaozinho_*`, `/bolaozinhos`, `/entrar`. **Internal code/tables stay English (`tournament*`)**.
- **Gates before every commit:** `ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest`. New pure domain `domain/tournament.py` MUST be added to the 100%-coverage gate.
- **HTML parse mode; `callback_data` ≤ 64 bytes; pt-BR copy.**
- **Maintenance rule:** any command/category/scoring/grading change updates `/ajuda` **and** COMPLETION.md in the same change.
- **Run commands with `python3`** in this environment (no `.venv` in the worktree; deps importable globally). Current Alembic head: `e3f4a5b6c7d8`.

---

## File Structure

| File | Responsibility |
|---|---|
| `tigrinho/enums.py` (modify) | Add `TournamentStatus` StrEnum. |
| `tigrinho/domain/tournament.py` (create) | Pure pot/prize/winner math + price/create parsing. 100% covered. |
| `tigrinho/db/models.py` (modify) | `Tournament`, `TournamentGame`, `TournamentEntry` models. |
| `tigrinho/db/migrations/versions/f1a2b3c4d5e6_add_tournaments.py` (create) | Create the 3 tables. |
| `tigrinho/db/repositories.py` (modify) | `TournamentRepository`. |
| `tigrinho/tournament_service.py` (create) | Auth, lock, pot/prize, `on_game_resolved`, correction/signature, announcements. |
| `tigrinho/config.py` (modify) | `tournament_currency`, `tournament_currency_decimals`, `reminder_max_mentions`, `bolaozinho_sweep_interval_minutes`. |
| `tigrinho/domain/text_pt.py` (modify) | `format_money_cents`, tournament renderers, capped-mention reminder block. |
| `tigrinho/bot/callbacks.py` (modify) | Tournament opcodes. |
| `tigrinho/bot/keyboards.py` (modify) | Tournament pickers/cards. |
| `tigrinho/bot/tournament_handlers.py` (create) | Commands + callbacks. |
| `tigrinho/bot/tournament_announce.py` (create) | Shared "post announcements" helper (used by poll/reconcile/sync/sweep). |
| `tigrinho/bot/sweep_job.py` (create) | Lock / stuck / rescue sweep. |
| `tigrinho/bot/poll_job.py`, `reconcile_job.py`, `sync_job.py` (modify) | Call resolution + post. |
| `tigrinho/bot/reminder_job.py` (modify) | Capped entrant mentions. |
| `tigrinho/bot/runtime.py` (modify) | `tournament_corrections`, `tournament_stuck_alerted` state. |
| `tigrinho/bot/app.py` (modify) | Register handlers, commands/scopes, schedule sweep. |
| `tigrinho/cli.py` (modify) | `bolaozinho` Typer sub-app. |
| Docs (modify) | COMPLETION.md §22/§17/§13/§19/§4.2, `/ajuda`, README, `config.example.yaml`, PROGRESS.md M12. |
| `tests/test_tournament_domain.py`, `test_tournament_repo.py`, `test_tournament_service.py`, `test_tournament_handlers.py`, `test_sweep_job.py` (create) + extend existing test files. |

> **Calibration note.** Complete code is given for the pure domain (Task 1) and the trickiest logic; for repository/handlers/CLI the plan gives exact signatures, behaviors, and test names (the implementer mirrors the established patterns in `repositories.py`, `bets_handlers.py`, `cli.py`).

---

## Task 1: Pure domain — pot/prize/winner math + parsing

**Files:** Create `tigrinho/domain/tournament.py`; Create `tests/test_tournament_domain.py`; Modify `pyproject.toml` (add `--cov=tigrinho.domain.tournament`).

**Interfaces — Produces:**
- `pot_cents(n_entrants: int, entry_price_cents: int) -> int`
- `prize_cents(n_entrants: int, entry_price_cents: int) -> int`  (= `max(0, (n-1)*p)`)
- `@dataclass(frozen) PrizeSplit(per_winner_cents: int, remainder_cents: int)`
- `split_prize(prize_cents: int, n_winners: int) -> PrizeSplit`
- `@dataclass(frozen) Winners(telegram_ids: tuple[int, ...], score: int)`
- `determine_winners(scores: Mapping[int, int]) -> Winners | None`
- `@dataclass(frozen) TournamentOutcome(has_result, pot_cents, prize_cents, winner_ids, winning_score, per_winner_cents, remainder_cents)`
- `compute_outcome(scores: Mapping[int, int], entry_price_cents: int) -> TournamentOutcome`
- `parse_price_to_cents(raw: str) -> int`  (raises `ValueError` on bad/≤0)
- `parse_create_args(arg: str) -> tuple[str, int]`  (raises `ValueError`; requires exactly one `|`, non-empty name)

- [ ] **Step 1: Write failing tests** in `tests/test_tournament_domain.py`:

```python
from __future__ import annotations

import pytest

from tigrinho.domain.tournament import (
    PrizeSplit,
    compute_outcome,
    determine_winners,
    parse_create_args,
    parse_price_to_cents,
    pot_cents,
    prize_cents,
    split_prize,
)


def test_pot_and_prize_basic() -> None:
    assert pot_cents(10, 1000) == 10000
    assert prize_cents(10, 1000) == 9000  # winner does not win own stake


def test_prize_lone_entrant_is_zero() -> None:
    assert prize_cents(1, 1000) == 0
    assert prize_cents(0, 1000) == 0


@pytest.mark.parametrize(
    ("prize", "n", "per", "rem"),
    [(9000, 1, 9000, 0), (9000, 2, 4500, 0), (9000, 3, 3000, 0), (1000, 3, 333, 1), (9000, 7, 1285, 5)],
)
def test_split_prize(prize: int, n: int, per: int, rem: int) -> None:
    assert split_prize(prize, n) == PrizeSplit(per, rem)


def test_split_prize_rejects_zero_winners() -> None:
    with pytest.raises(ValueError):
        split_prize(9000, 0)


def test_determine_winners_single() -> None:
    w = determine_winners({1: 14, 2: 12, 3: 9})
    assert w is not None and w.score == 14 and w.telegram_ids == (1,)


def test_determine_winners_tie_sorted() -> None:
    w = determine_winners({3: 12, 1: 12, 2: 5})
    assert w is not None and w.score == 12 and w.telegram_ids == (1, 3)


def test_determine_winners_all_zero_all_win() -> None:
    w = determine_winners({1: 0, 2: 0})
    assert w is not None and w.telegram_ids == (1, 2) and w.score == 0


def test_determine_winners_empty_is_none() -> None:
    assert determine_winners({}) is None


def test_compute_outcome_single_winner() -> None:
    o = compute_outcome({1: 14, 2: 12}, 1000)
    assert o.has_result and o.pot_cents == 2000 and o.prize_cents == 1000
    assert o.winner_ids == (1,) and o.per_winner_cents == 1000 and o.remainder_cents == 0


def test_compute_outcome_no_entrants() -> None:
    o = compute_outcome({}, 1000)
    assert o.has_result is False and o.pot_cents == 0 and o.winner_ids == ()


@pytest.mark.parametrize(
    ("raw", "cents"),
    [("10", 1000), ("10,50", 1050), ("10.50", 1050), (" 7 ", 700), ("0,99", 99), ("100", 10000)],
)
def test_parse_price_ok(raw: str, cents: int) -> None:
    assert parse_price_to_cents(raw) == cents


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "1,234", "1.2.3", "10,5,0"])
def test_parse_price_bad(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_price_to_cents(raw)


def test_parse_create_args_ok() -> None:
    assert parse_create_args("Oitavas de final | 10,50") == ("Oitavas de final", 1050)


@pytest.mark.parametrize("arg", ["semprice", "a | b | c", "| 10", "Nome |", "  | 10"])
def test_parse_create_args_bad(arg: str) -> None:
    with pytest.raises(ValueError):
        parse_create_args(arg)
```

- [ ] **Step 2: Run, verify they fail** — `python3 -m pytest tests/test_tournament_domain.py -q` → import error.

- [ ] **Step 3: Implement `tigrinho/domain/tournament.py`** (pure; only `dataclasses`, `collections.abc`, `re`):

```python
"""Pure bolãozinho (tournament) math: pot, prize, winners, and input parsing (Feature 7 / §22).

No I/O, clock, or DB — deterministic and fully covered. Money is integer cents; the winner does
NOT win their own stake, so prize = pot − one entry, split equally among tied winners.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass


def pot_cents(n_entrants: int, entry_price_cents: int) -> int:
    return n_entrants * entry_price_cents


def prize_cents(n_entrants: int, entry_price_cents: int) -> int:
    """Pot minus one entry — the winner keeps their own stake (0 for a lone/empty entrant set)."""
    return max(0, (n_entrants - 1) * entry_price_cents)


@dataclass(frozen=True, slots=True)
class PrizeSplit:
    per_winner_cents: int
    remainder_cents: int


def split_prize(prize_cents_total: int, n_winners: int) -> PrizeSplit:
    if n_winners < 1:
        raise ValueError("n_winners must be >= 1")
    per = prize_cents_total // n_winners
    return PrizeSplit(per, prize_cents_total - per * n_winners)


@dataclass(frozen=True, slots=True)
class Winners:
    telegram_ids: tuple[int, ...]  # sorted ascending for determinism
    score: int


def determine_winners(scores: Mapping[int, int]) -> Winners | None:
    if not scores:
        return None
    top = max(scores.values())
    ids = tuple(sorted(tid for tid, s in scores.items() if s == top))
    return Winners(ids, top)


@dataclass(frozen=True, slots=True)
class TournamentOutcome:
    has_result: bool
    pot_cents: int
    prize_cents: int
    winner_ids: tuple[int, ...]
    winning_score: int
    per_winner_cents: int
    remainder_cents: int


def compute_outcome(scores: Mapping[int, int], entry_price_cents: int) -> TournamentOutcome:
    n = len(scores)
    pot = pot_cents(n, entry_price_cents)
    prize = prize_cents(n, entry_price_cents)
    winners = determine_winners(scores)
    if winners is None:
        return TournamentOutcome(False, pot, prize, (), 0, 0, 0)
    split = split_prize(prize, len(winners.telegram_ids))
    return TournamentOutcome(
        True, pot, prize, winners.telegram_ids, winners.score,
        split.per_winner_cents, split.remainder_cents,
    )


_PRICE_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")


def parse_price_to_cents(raw: str) -> int:
    """Parse a decimal price in the global currency to cents. Raises ValueError on bad/≤0."""
    text = raw.strip().replace(".", ",")
    if not _PRICE_RE.match(raw.strip()):
        raise ValueError(f"invalid price: {raw!r}")
    whole, _, frac = text.partition(",")
    cents = int(whole) * 100 + (int(frac.ljust(2, "0")) if frac else 0)
    if cents <= 0:
        raise ValueError("price must be > 0")
    return cents


def parse_create_args(arg: str) -> tuple[str, int]:
    """Split '<name> | <price>' — exactly one '|', non-empty name. Raises ValueError otherwise."""
    if arg.count("|") != 1:
        raise ValueError("expected exactly one '|'")
    name_raw, _, price_raw = arg.partition("|")
    name = name_raw.strip()
    if not name:
        raise ValueError("empty name")
    return name, parse_price_to_cents(price_raw)
```

- [ ] **Step 4: Add to coverage gate** — in `pyproject.toml` `addopts`, append `--cov=tigrinho.domain.tournament`.
- [ ] **Step 5: Run gates** — `ruff check . && ruff format . && python3 -m mypy --strict tigrinho/domain/tournament.py && python3 -m pytest tests/test_tournament_domain.py -q` → all pass, tournament.py 100%.
- [ ] **Step 6: Commit** — `feat(domain): pure bolãozinho pot/prize/winner math + parsing`.

---

## Task 2: Config settings + money formatting

**Files:** Modify `tigrinho/config.py`, `config.example.yaml`, `tigrinho/domain/text_pt.py`; extend `tests/test_config.py`, `tests/test_text_pt.py`.

**Interfaces — Produces:**
- Settings: `tournament_currency: str = "R$"`, `tournament_currency_decimals: int = Field(default=2, ge=0)`, `reminder_max_mentions: int = Field(default=20, gt=0)`, `bolaozinho_sweep_interval_minutes: int = Field(default=10, gt=0)`.
- `text_pt.format_money_cents(cents: int, *, currency: str, decimals: int = 2) -> str` → e.g. `"R$ 90,00"` (pt-BR comma; thousands not grouped for simplicity).

- [ ] **Step 1:** Test `format_money_cents(9000, currency="R$") == "R$ 90,00"`, `(99, currency="R$") == "R$ 0,99"`, `(1285, currency="US$") == "US$ 12,85"`, decimals=0 → `"R$ 90"`.
- [ ] **Step 2:** Test config defaults present (extend `test_config.py` asserting the 4 new fields default correctly).
- [ ] **Step 3:** Implement `format_money_cents` in `text_pt.py` (integer math: `whole, frac = divmod(cents, 10**decimals)`; join with `,`).
- [ ] **Step 4:** Add the 4 settings to `config.py`; add them (commented, with defaults) to `config.example.yaml`.
- [ ] **Step 5:** Gates green. **Commit** — `feat(config,text): tournament currency/settings + money formatting`.

---

## Task 3: ORM models + Alembic migration

**Files:** Modify `tigrinho/enums.py`, `tigrinho/db/models.py`; Create `tigrinho/db/migrations/versions/f1a2b3c4d5e6_add_tournaments.py`; extend `tests/test_models.py` (migration test auto-covers schema match).

**Interfaces — Produces (models):**
- `TournamentStatus(StrEnum)`: `DRAFT, OPEN, FINISHED, CANCELLED`.
- `Tournament`: `id` PK autoinc; `name` str; `entry_price_cents` int; `status` Enum; `created_by` BigInteger; `created_at`; `opened_at?`; `locked_at?`; `result_announced_at?`; `result_signature: str|None`; `correction_count` int default 0. Relationships: `games` (→ TournamentGame), `entries` (→ TournamentEntry), both `cascade="all, delete-orphan"`.
- `TournamentGame`: `tournament_id` FK PK-part, `fixture_id` FK→games PK-part (`__table_args__` PK over both, via `mapped_column(primary_key=True)` on both).
- `TournamentEntry`: `id` PK; `tournament_id` FK; `player_telegram_id` BigInteger FK→players; `joined_at`; `UniqueConstraint(tournament_id, player_telegram_id, name="uq_entry_one_per_player")`.
- Add all three + `TournamentStatus` to `models.__all__`.

- [ ] **Step 1:** Add `TournamentStatus` to `enums.py`; re-export from `models.py` (like `GameStatus`).
- [ ] **Step 2:** Add the three models (mirror existing `Mapped[...]` style; `BigInteger` for telegram ids).
- [ ] **Step 3:** Run `python3 -m pytest tests/test_migrations.py -q` → **FAILS** (ORM has tables the migration lacks).
- [ ] **Step 4:** Hand-write the migration (`down_revision = "e3f4a5b6c7d8"`) creating `tournaments`, `tournament_games`, `tournament_entries` with exact columns/constraints; `downgrade` drops them in FK-safe order.
- [ ] **Step 5:** `python3 -m pytest tests/test_migrations.py tests/test_models.py -q` → pass; `python3 -m alembic heads` shows the new head.
- [ ] **Step 6:** Gates green. **Commit** — `feat(db): tournament tables + migration`.

---

## Task 4: TournamentRepository

**Files:** Modify `tigrinho/db/repositories.py`; Create `tests/test_tournament_repo.py`.

**Interfaces — Produces** (`TournamentRepository(session)`):
- `create(name, entry_price_cents, created_by) -> Tournament` (status DRAFT).
- `get(id) -> Tournament | None`; `list_all() -> list[Tournament]`; `list_by_status(*statuses) -> list[Tournament]`.
- `add_game(tournament_id, fixture_id) -> None` (idempotent); `remove_game(...) -> None`; `list_game_ids(tournament_id) -> list[int]`; `list_games(tournament_id) -> list[Game]` (joined, ordered by kickoff).
- `tournaments_for_game(fixture_id) -> list[Tournament]` (all containing it).
- `add_entry(tournament_id, player_telegram_id) -> bool` (False if already entered); `remove_entry(...) -> bool`; `count_entries(tournament_id) -> int`; `entry_ids(tournament_id) -> list[int]`; `is_entered(tournament_id, telegram_id) -> bool`.
- `earliest_kickoff(tournament_id) -> datetime | None` (min member-game kickoff_utc).
- `all_games_resolved(tournament_id) -> bool` (every member game status in {FINISHED, VOID}; **False if no games**).
- `standings(tournament_id) -> dict[int, int]`: `{entry_telegram_id: sum(points_awarded)}` over **entrants only**, summing graded non-void member-game bets (LEFT JOIN so a no-bet entrant scores 0).
- `list_open_with_member_game_settled_unannounced() -> list[Tournament]` and helpers used by the sweep/service (`list_active()` = DRAFT|OPEN).
- Mutators: `set_status`, `set_price`, `set_opened`, `set_locked(when)`, `set_announced(when, signature)`, `bump_correction()`.

- [ ] **Step 1:** Write `tests/test_tournament_repo.py` covering: create/get/list; add/remove game idempotency; M:N (`tournaments_for_game`); unique entry (`add_entry` twice → False); `count_entries`; `earliest_kickoff`; `all_games_resolved` (False with no games / one SCHEDULED, True when all FINISHED/VOID); `standings` sums only entrants' graded non-void member-game points and gives 0 to a no-bet entrant and **excludes a non-entrant who bet**.
- [ ] **Step 2:** Run → fail (no class).
- [ ] **Step 3:** Implement `TournamentRepository` (mirror existing repo style; `standings` via a `select(...).join(...).where(TournamentEntry...).group_by(...)` with `func.coalesce(func.sum(...), 0)`; exclude void games by `Game.status != VOID` and ungraded by `Bet.settled_at.is_not(None)`).
- [ ] **Step 4:** Run repo tests → pass.
- [ ] **Step 5:** Gates green. **Commit** — `feat(db): TournamentRepository`.

---

## Task 5: tournament_service — management ops + locking + auth

**Files:** Create `tigrinho/tournament_service.py`; Create `tests/test_tournament_service.py`.

**Interfaces — Produces:**
- `class TournamentError(Exception)` with a pt-BR `message`.
- `create_tournament(session, *, name, entry_price_cents, created_by) -> Tournament`.
- `can_manage(t: Tournament, actor_id: int, admin_id: int) -> bool` (`actor_id == t.created_by or actor_id == admin_id`).
- `require_manage(t, actor_id, admin_id) -> None` (raises `TournamentError` "Só quem criou…").
- `add_game(session, t, fixture_id, *, now)` — guards: manageable, not locked, game exists + `SCHEDULED` + `kickoff_utc > now`; else `TournamentError`.
- `remove_game(...)`, `set_price(session, t, cents, *, now)` — price guard: not locked **and** `count_entries == 0`.
- `open_tournament(session, t, *, now)` — guards: status DRAFT, price>0, ≥1 game, no game started; sets `OPEN` + `opened_at`.
- `cancel_tournament(session, t)` — sets `CANCELLED`.
- `join(session, t, *, telegram_id, display_name, now) -> JoinResult` — guards: status OPEN and not locked (`is_locked(t, now)`); auto-creates player; freezes price (no-op: price already on tournament — freeze is enforced by `set_price`); returns `JoinResult(already, pot_cents, prize_cents, game_ids)`.
- `is_locked(t, now, repo) -> bool` — `t.locked_at is not None or (earliest_kickoff is not None and now >= earliest_kickoff)`.
- `apply_lock(session, repo, now)` — set `locked_at` (persisted, one-way) for any OPEN tournament whose earliest kickoff passed (used by sweep + opportunistically).

- [ ] **Step 1:** Tests: creator/admin can manage, others get `TournamentError` (F11); `set_price` rejected once an entry exists (F12); `add_game` rejected for started/non-scheduled/locked (F1 lock); `join` rejected after lock (F1); `open_tournament` precondition failures; `apply_lock` sets `locked_at` once and a later kickoff change doesn't clear it (F12 one-way).
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement the service functions (use `TournamentRepository`, `PlayerRepository`, `GameRepository`; raise `TournamentError` with pt-BR messages; never commit — callers own the unit of work, mirroring repositories).
- [ ] **Step 4:** Run service tests → pass.
- [ ] **Step 5:** Gates green. **Commit** — `feat(service): bolãozinho management + locking + auth`.

---

## Task 6: tournament_service — resolution, outcome, announcements, corrections

**Files:** Modify `tigrinho/tournament_service.py`; extend `tests/test_tournament_service.py`.

**Interfaces — Produces:**
- `@dataclass WinnerLine(telegram_id, display_name, score)`.
- `@dataclass TournamentWinnerAnnouncement(tournament_id, name, pot_cents, prize_cents, winners: tuple[WinnerLine,...], per_winner_cents, remainder_cents, is_correction)`.
- `@dataclass TournamentNoResultAnnouncement(tournament_id, name)`.
- `TournamentAnnouncement = TournamentWinnerAnnouncement | TournamentNoResultAnnouncement`.
- `on_game_resolved(session, fixture_id) -> list[TournamentAnnouncement]` — for each tournament containing `fixture_id`:
  1. if `all_games_resolved` is False → continue.
  2. compute `standings` → `compute_outcome(scores, t.entry_price_cents)`.
  3. build a stable `result_signature` = `f"{sorted(winner_ids)}|{per_winner_cents}|{remainder_cents}|{has_result}"`.
  4. if no scorable result (no entrants OR all games void → `not outcome.has_result or prize underlying games all void`): set `CANCELLED`; if it was unannounced (or signature changed from a prior announce) emit `TournamentNoResultAnnouncement` once; set `result_announced_at` + signature.
  5. else if unannounced → set `FINISHED`, `result_announced_at`, signature; emit `TournamentWinnerAnnouncement(is_correction=False)`.
  6. else if announced and signature changed (re-grade or revival) → set new signature; emit `TournamentWinnerAnnouncement(is_correction=True)` (revival also moves a `CANCELLED` back through `FINISHED`).
  - `display_name` for winners via `PlayerRepository`.
- `signature_of(outcome) -> str` helper (pure-ish, in service).

- [ ] **Step 1:** Tests (temp DB): all-settled single winner → one winner announcement, then re-run is idempotent (no second announcement, same signature); **last game VOID** (not settled) still finishes (F4) — set the last member game VOID and assert an announcement is produced; **zero entrants** → no-result + CANCELLED (F10); **all games void** → no-result + CANCELLED; a re-grade that flips the winner after announce → correction announcement (F8); un-void of an all-void CANCELLED tournament that then settles → revives + announces (F5).
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement `on_game_resolved` + dataclasses + signature.
- [ ] **Step 4:** Run → pass.
- [ ] **Step 5:** Gates green. **Commit** — `feat(service): on_game_resolved + outcome/announce/correction`.

---

## Task 7: Callbacks + keyboards + shared announce helper

**Files:** Modify `tigrinho/bot/callbacks.py`, `tigrinho/bot/keyboards.py`; Create `tigrinho/bot/tournament_announce.py`; extend `tests/test_callbacks.py`, `tests/test_keyboards.py`.

**Interfaces — Produces (callbacks, all ≤64 bytes):**
- `TournamentAddToggle(tournament_id, fixture_id)` → `bg:<tid>:<fixture_id>` (identity-based — F18; writes membership immediately).
- `TournamentCardDone(tournament_id)` → `bd:<tid>`.
- `TournamentOpen(tournament_id)` → `bo:<tid>`.
- `TournamentCancel(tournament_id)` → `bx:<tid>`.
- `TournamentJoinPick(tournament_id)` → `bj:<tid>` (show join card).
- `TournamentJoinConfirm(tournament_id)` → `bk:<tid>`.
- `TournamentDetails(tournament_id)` → `bi:<tid>`.
- Extend `CallbackData` union + `encode`/`decode` + opcode docstring.

**Interfaces — Produces (announce helper):**
- `async post_tournament_announcements(app_context, context, anns: list[TournamentAnnouncement]) -> None` — renders each via `text_pt` (winner mentions via `tg://user?id=`), best-effort group send with the §9.5-style correction cap using `app_context.tournament_corrections` (cap repeated corrections; DM admin once past cap).

- [ ] **Step 1:** Test each new callback `encode`→`decode` round-trips and stays ≤64 bytes (extend `test_callbacks.py`, mirroring existing param tests).
- [ ] **Step 2:** Run → fail. **Step 3:** Implement callbacks + keyboards (picker built from `GameRepository.list_upcoming` with `✅`/`☐` per current membership; card/list/details keyboards). **Step 4:** Run → pass.
- [ ] **Step 5:** Implement `tournament_announce.py` (+ add `tournament_corrections: dict[int,int]` and `tournament_stuck_alerted: set[int]` to `runtime.AppContext`). **Step 6:** Gates green. **Commit** — `feat(bot): tournament callbacks, keyboards, announce helper`.

---

## Task 8: Bot handlers + command registration

**Files:** Create `tigrinho/bot/tournament_handlers.py`; Modify `tigrinho/bot/app.py`; Modify `tigrinho/domain/text_pt.py` (renderers); Create `tests/test_tournament_handlers.py`.

**Interfaces — Produces:**
- `register_tournament_handlers(application)` — `CommandHandler`s for `bolaozinho_criar`, `bolaozinho_preco`, `bolaozinho_abrir`, `bolaozinhos`, `bolaozinho`, `entrar`, plus a `CallbackQueryHandler(pattern="^(bg|bd|bo|bx|bj|bk|bi):")` dispatcher. Each management callback re-checks `can_manage` (F11).
- `text_pt` renderers: `tournament_card_text`, `tournament_list_text`, `tournament_details_text` (incl. live mini-standings), `tournament_announcement_text` (publish), `entry_card_text`, `tournament_result_text`, `tournament_no_result_text`, `tournament_correction_text`.
- `app.py`: add the commands to `PRIVATE_COMMANDS` + `GROUP_COMMANDS`, register the handler set in `build_application` (before the bet wizard catch-all, like the board handlers), and `schedule_sweep_job` in `post_init`.

- [ ] **Step 1:** Thin flow tests (FakeProvider/temp DB, mirroring `test_bets_handlers.py`): `/bolaozinho_criar Nome | 10` creates DRAFT + card; create with bad/missing `|` → usage error (F19); non-creator tapping a management callback → refusal (F11); `/entrar` join flow creates entry + shows games; join after lock → refusal.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement handlers + renderers + registration. **Step 4:** Run → pass.
- [ ] **Step 5:** Gates green (incl. `test_app.py` command-scope assertions — update them). **Commit** — `feat(bot): bolãozinho commands + handlers + /ajuda not yet`.

---

## Task 9: Sweep job + wire resolution into poll/reconcile/sync

**Files:** Create `tigrinho/bot/sweep_job.py`; Modify `tigrinho/bot/poll_job.py`, `reconcile_job.py`, `sync_job.py`, `app.py`; Create `tests/test_sweep_job.py`; extend `tests/test_poll_job.py`, `test_reconcile_job.py`, `test_sync_job.py`.

**Interfaces — Produces:**
- `sweep_job(context)` + `schedule_sweep_job(job_queue, settings)` (`run_repeating` every `bolaozinho_sweep_interval_minutes*60`): (a) `apply_lock` for OPEN tournaments past earliest kickoff; (b) for tournaments whose member games are all resolved but unannounced → `on_game_resolved` for one member fixture and post (F4/F13 backstop); (c) DM admin a bolãozinho-aware escalation for a member game stuck past `kickoff + match_window_hours` (dedup via `tournament_stuck_alerted`).
- Hook calls: in `poll_job._settle_and_announce` (after settle commit) and `reconcile_job._reconcile_one` (after re-grade commit) and `sync_job.sync_job` (after `_run_sync`, for each voided + rescheduled fixture) → call `on_game_resolved` in a fresh session and `post_tournament_announcements`.
- F8 reconcile widening: add `TournamentRepository.member_games_to_reconcile(now)` returning settled member games of non-terminal tournaments regardless of window; include them in `reconcile_job` `due` set.

- [ ] **Step 1:** Tests: sweep sets `locked_at` once; sweep finishes a tournament whose last game was VOIDed (F4); sync voiding the last member game triggers a winner/no-result announcement (F4 via sync path); reconcile re-grade posts a tournament correction (F8); stuck escalation DMs admin once (F13).
- [ ] **Step 2:** Run → fail. **Step 3:** Implement sweep + hooks. **Step 4:** Run → pass.
- [ ] **Step 5:** Gates green. **Commit** — `feat(bot): bolãozinho sweep job + resolution hooks (F4/F5/F8/F13)`.

---

## Task 10: Reminder integration (capped mentions)

**Files:** Modify `tigrinho/bot/reminder_job.py`, `tigrinho/domain/text_pt.py`; extend `tests/test_reminder_job.py`, `test_text_pt.py`.

**Interfaces — Produces:**
- `reminder_text(...)` gains an optional per-game tournament block: a deduped, capped (`reminder_max_mentions`, `… +N`) list of entrant mentions for entrants who have **not** bet on that game, across `OPEN` tournaments containing it.
- `reminder_job._run_reminder`: build entrant-mention data via `TournamentRepository`; tournament games are reminder-eligible even without `announced_at`; on a permanent oversized-send failure, **still `mark_reminded`** (or send trimmed) so it never retry-spams (F17).

- [ ] **Step 1:** Tests: a reminder for a tournament game mentions a non-betting entrant; mentions deduped across two overlapping tournaments and capped with `… +N` (F17); reminder still works for a tournament game with `announced_at IS NULL`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** Run → pass.
- [ ] **Step 5:** Gates green. **Commit** — `feat(bot): merge capped bolãozinho mentions into the 1h reminder`.

---

## Task 11: Admin CLI (`bolaozinho` sub-app)

**Files:** Modify `tigrinho/cli.py`; extend `tests/test_cli.py`.

**Interfaces — Produces:** a Typer sub-app `bolaozinho` with `create`, `list`, `show`, `add-game`, `remove-game`, `set-price`, `cancel` (confirm flag), `entries`, `add-entry`, `remove-entry`, `recompute`, `announce`, `sweep`. Readable tables; destructive ops need `--yes`.

- [ ] **Step 1:** Tests (CliRunner, mirroring `test_cli.py`): `create` then `list` shows it; `add-game`; `entries`; `cancel` requires `--yes`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** Run → pass.
- [ ] **Step 5:** Gates green. **Commit** — `feat(cli): bolãozinho admin commands`.

---

## Task 12: Docs (COMPLETION.md §22, /ajuda, rules, CLI, assumptions, README, PROGRESS)

**Files:** Modify `COMPLETION.md`, `tigrinho/bot/help_handlers.py` (the `/ajuda` text), `README.md`, `config.example.yaml` (if not already), `PROGRESS.md`; extend `tests/test_app.py`/`test_help` if `/ajuda` is asserted.

- [ ] **Step 1:** Add **COMPLETION.md §22 — Feature 7: Bolãozinhos** (commands, lifecycle, money/prize rule, reminder, CLI) + §21 change-log entry; update §17 (rules), §13 (CLI), §19 (assumptions: create/read/join open, management creator/admin-only), §4.2 (new settings).
- [ ] **Step 2:** Update `/ajuda` text in `help_handlers.py` with the bolãozinho section (commands, prize = pot − one entry / split, "bets close at kickoff", "joining closes at first game"). Update any `/ajuda` snapshot test.
- [ ] **Step 3:** README player/admin guide: bolãozinho commands; PROGRESS.md: add **M12 — Bolãozinhos** block, ticked.
- [ ] **Step 4:** Gates green (full `pytest`). **Commit** — `docs: bolãozinhos in COMPLETION.md, /ajuda, README, PROGRESS (§22)`.

---

## Task 13: Full-suite green + smoke

- [ ] **Step 1:** Run the complete gate set: `ruff check . && ruff format --check . && python3 -m mypy --strict . && python3 -m pytest`. Fix anything red.
- [ ] **Step 2:** Manual reasoning pass over the spec's §11 testing checklist — confirm each fix (F18/F11/F4/F5/F8/F17/F12/F10 + no-late-join + prize math + no-`|`) has a passing test.
- [ ] **Step 3:** Final **Commit** if any fixups — `chore: bolãozinhos full-suite green`.

---

## Self-Review (run after writing — checklist)

- **Spec coverage:** §3 tables → Task 3; §4 lifecycle/locks/permissions → Tasks 5/9; §5 commands → Task 8; §6 money → Tasks 1/2; §7 resolution/corrections → Tasks 6/9; §8 reminder → Task 10; §9 CLI → Task 11; §10 docs → Task 12; §11 tests → spread across tasks; §12 defaults → Tasks 1/5/6. No gaps.
- **Placeholders:** none — domain code is complete; other tasks give exact signatures + named tests.
- **Type consistency:** `on_game_resolved` returns `list[TournamentAnnouncement]` (Task 6) consumed by `post_tournament_announcements` (Task 7) and the hooks (Task 9); `compute_outcome` (Task 1) consumed by Task 6; `TournamentRepository.standings` (Task 4) consumed by Task 6. Names align.
