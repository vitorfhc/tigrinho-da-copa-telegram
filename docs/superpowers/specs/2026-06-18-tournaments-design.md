# Tournaments — Design Spec

**Date:** 2026-06-18
**Status:** Approved for planning
**Feature:** Feature 7 — Tournaments (becomes COMPLETION.md §22)

---

## 1. Summary

A **tournament** is an admin-/group-created competition over a fixed set of World Cup fixtures with a
real-money **entry price**. Players **enter** via a slash command; the **pot** = (number of entrants ×
entry price). When every game in the tournament has finished and been graded, the bot posts a group
message naming the **winner(s)** and the **payout**. The winner is whoever has the most points across
the tournament's games (using the existing bet points, 5/2/2/2/1); **ties split the pot evenly**.

The bot is **bookkeeping only** — it never moves real money. It tracks entrants, computes the pot,
and announces who won how much; the friends settle offline (consistent with the project being
otherwise "no-money", but tournaments add an explicit, opt-in cash side-pot).

### Product decisions (locked via clarifying questions, 2026-06-18)

| # | Decision | Choice |
|---|---|---|
| 1 | Concurrency / membership | **Multiple tournaments can be active at once; a game may belong to many tournaments** (many-to-many). |
| 2 | Join window | **Open until all the tournament's games have ended.** |
| 3 | Money & currency | **Bookkeeping only**; **one global currency** in `config.yaml`. |
| 4 | Pot-split rounding | **2-decimal split, show the remainder** (e.g. `R$ 3,33 cada (sobra R$ 0,01)`). |
| 5 | Who can manage tournaments | **Anyone in the group**; commands work in group + DM. |
| 6 | Scoring & ties | **Reuse existing bet points**; **pure points-equality** splits the pot (NO board tie-breaks). |
| 7 | 1h-before notification | **Merged into the existing §9.3 reminder**, adding entrant @-mentions. |
| 8 | When to announce winner | **When all tournament games are settled** (robust to out-of-order finishes); corrected by §9.5 re-grades. |

---

## 2. Architecture & integration points

This feature layers onto the completed M0–M11 build. It introduces a new **M12 — Tournaments**
milestone in `PROGRESS.md`. It reuses, and must stay consistent with, these existing surfaces:

- **Settlement** flows through `tigrinho/settlement_service.py::settle_fixture`, called by **both**
  `bot/poll_job.py::_settle_and_announce` (auto-settle) and the reconcile job (`bot/reconcile_job.py`,
  §9.5). The tournament "all games settled?" check hooks **after** each of these settle a fixture.
- **Standings** are derived purely from `bets.points_awarded` (see `settlement_service` and the board).
  Tournaments follow the same rule — no denormalized per-entrant score is stored.
- **The 1h reminder** lives in `bot/reminder_job.py::_run_reminder` + `domain/text_pt.py::reminder_text`.
- **Commands** register in `bot/app.py` (`build_application`, `PRIVATE_COMMANDS`, `GROUP_COMMANDS`,
  `set_commands`).
- **Settings** are pydantic-validated in `tigrinho/config.py`.
- **Money must never be a float.** Entry price and pot are integer **minor units (cents)** everywhere;
  only the display layer renders a decimal string.

### New / changed modules

| Module | Purpose |
|---|---|
| `tigrinho/domain/tournament.py` | **Pure** scoring, winner selection, and pot splitting (~100% line+branch coverage). No I/O, clock, or DB. |
| `tigrinho/db/models.py` | Add `Tournament`, `TournamentGame`, `TournamentEntry` ORM models. |
| `tigrinho/db/migrations/versions/<rev>_add_tournaments.py` | New append-only Alembic migration. |
| `tigrinho/db/repositories.py` | Add `TournamentRepository` (CRUD, membership, entries, standings queries). |
| `tigrinho/tournament_service.py` | Telegram-agnostic orchestration: lock checks, pot computation, `on_game_settled`, outcome signature/correction. Shared by bot + CLI. |
| `tigrinho/bot/tournament_handlers.py` | Slash commands + inline-keyboard pickers/cards (stateless `callback_data`). |
| `tigrinho/bot/keyboards.py` | Add tournament pickers/cards keyboards. |
| `tigrinho/bot/callbacks.py` | Add tournament opcodes to the codec (all payloads ≤ 64 bytes). |
| `tigrinho/domain/text_pt.py` | Add tournament render functions + money formatting; extend `reminder_text`. |
| `tigrinho/bot/poll_job.py`, `tigrinho/bot/reconcile_job.py` | Call `tournament_service.on_game_settled` after settling; post winner/correction. |
| `tigrinho/bot/reminder_job.py` | Merge entrant mentions into the reminder. |
| `tigrinho/cli.py` | Add a `tournament` Typer sub-app (CLI parity, §13). |
| `tigrinho/config.py`, `config.example.yaml` | Add `tournament_currency` (+ optional `tournament_currency_decimals`). |

---

## 3. Data model

One new Alembic migration (append-only; never edit existing migrations). All datetimes are naive UTC
per the project convention (`utcnow()`); money is integer cents.

### `tournaments`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK autoincrement | The handle used in commands. |
| `name` | TEXT | Human label (e.g. "Oitavas de final"). Not required unique. |
| `entry_price_cents` | INTEGER, `> 0` | Money in minor units of `tournament_currency`. |
| `status` | TEXT enum | `DRAFT` \| `OPEN` \| `FINISHED` \| `CANCELLED`. |
| `created_by` | BIGINT | telegram_id of the creator (record only). |
| `created_at` | TIMESTAMP | UTC. |
| `opened_at` | TIMESTAMP NULL | Set when published (`OPEN`). |
| `result_announced_at` | TIMESTAMP NULL | Idempotency for the winner post. |
| `result_signature` | TEXT NULL | Stable hash of the announced outcome (winners set + per-winner cents + remainder), to detect a §9.5 re-grade flipping the result. |
| `correction_count` | INTEGER default 0 | Caps oscillating corrections (mirrors §9.5). |

### `tournament_games` (M:N)
| Column | Type | Notes |
|---|---|---|
| `tournament_id` | INTEGER FK → tournaments.id | |
| `fixture_id` | INTEGER FK → games.fixture_id | |
| PK | (`tournament_id`, `fixture_id`) | A game may be in many tournaments. |

### `tournament_entries`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `tournament_id` | INTEGER FK → tournaments.id | |
| `player_telegram_id` | BIGINT FK → players.telegram_id | Joining auto-creates the `Player` row (like a first bet). |
| `joined_at` | TIMESTAMP | UTC. |
| UNIQUE | (`tournament_id`, `player_telegram_id`) | One entry per player per tournament. |

> Cascade: deleting a tournament (admin/CLI only) cascades to its `tournament_games` and
> `tournament_entries`. Games are never row-deleted in normal operation — a postponed/cancelled
> fixture is set to `status = VOID` (§9.1), so its `tournament_games` row persists and the void game is
> simply excluded from scoring (§6, §7).

---

## 4. Lifecycle & rules

```
DRAFT ──/torneio_abrir──▶ OPEN ──(all games settled-or-void)──▶ FINISHED
  │                         │
  └──── ❌ Cancelar ────────┴────────────────────────────────▶ CANCELLED
```

- **`DRAFT`** — created by `/torneio_criar`. Games and price are editable. Not joinable, not announced.
- **`OPEN`** — published via `/torneio_abrir` (posts the group "novo torneio" announcement). Joinable.
- **First-kickoff lock** — once the tournament's **earliest game** has `now >= kickoff_utc`, the games
  list and the price are **frozen** (no add/remove/price change). Joining **stays open** (decision 2).
- **`FINISHED`** — set when all games are settled-or-void and the winner post (or "sem resultado"
  notice) has been emitted.
- **`CANCELLED`** — admin escape hatch (`❌ Cancelar` / CLI), or auto on settlement when there are
  zero valid games or zero entrants.

### Invariants (enforced in `tournament_service`, with clear pt-BR refusals)
- A game may be added only if its status is `SCHEDULED` and `kickoff_utc > now`.
- No game may be added/removed after the tournament's first game has kicked off.
- Price must be set (`> 0`) and at least one game must be added before `/torneio_abrir`.
- `/torneio_abrir` is allowed only from `DRAFT` and only if no game has started yet.
- Entries are accepted only while `status == OPEN` and not all games have ended.
- `result_announced_at` makes the winner post idempotent (never double-posted).

---

## 5. Commands (pt-BR)

All tournament commands are usable by **anyone in the group**, in **group or DM**. They are registered
in both `PRIVATE_COMMANDS` and `GROUP_COMMANDS` with the appropriate scopes. Inline pickers/cards use
stateless `callback_data` (≤ 64 bytes), decoded by `bot/callbacks.py` — consistent with `/apostar`
and `/placar_jogos`.

### Management
- **`/torneio_criar <nome> | <preço>`** — create a `DRAFT`. Price parsed as a decimal in
  `tournament_currency` → cents (accepts `10`, `10,50`, `10.50`). Replies with a **management card**:
  game count, price, status, and buttons `➕ Adicionar jogos`, `🗑 Remover jogo`, `📣 Abrir`,
  `❌ Cancelar`.
- **`➕ Adicionar jogos`** — opens a **multi-select picker** of upcoming (`SCHEDULED`, future) games
  (most-imminent first, ~10), `☐`/`✅` toggling via a stateless bitmask over the picker position
  (same pattern as `/placar_jogos`), then `✅ Salvar`. Adds the selected games to the tournament.
- **`🗑 Remover jogo`** — picker of the tournament's current games; removing allowed only before lock.
- **`/torneio_preco <id> <preço>`** — set/adjust the price (DRAFT/pre-lock only).
- **`/torneio_abrir <id>`** (or the `📣 Abrir` button) — publish: set `OPEN`, post the group
  announcement (`🏆 Novo torneio: <nome> — entrada <preço> — N jogos … — use /entrar para participar`)
  with one `🎯 Apostar` deep-link per game (reuses `announcement_keyboard`).

### Joining
- **`/entrar`** — lists `OPEN`, still-joinable tournaments as buttons (or, if exactly one, jumps
  straight to it). Tapping shows the games + kickoffs, entry price, **current pot**, entrant count, and
  `✅ Entrar (<preço>)`. Confirming creates the entry (auto-creating the `Player`), then shows
  **which games to bet on** with `🎯 Apostar` deep-links. Re-entering is a no-op with a friendly note.

### Views (group + DM)
- **`/torneios`** — list tournaments (DRAFT/OPEN/FINISHED) with status, pot, entrant count; tapping one
  opens its details card.
- **`/torneio <id>`** — details: games (with kickoffs/results), price, pot, entrants, a **live
  mini-standings** among entrants (derived from settled bets so far), and the caller's own
  entered/bet status.

### Command registration & `/ajuda`
`/ajuda` MUST be updated to describe tournaments (how they work, the commands, the money/pot/split
rule, "bets still close at kickoff"). Per the maintenance rule, the same change updates COMPLETION.md.

---

## 6. Scoring, winner & money — pure domain (`domain/tournament.py`)

Pure, deterministic, no I/O — target ~100% line+branch coverage (same bar as `scoring.py`).

- **Entrant score** = Σ `points_awarded` over that entrant's bets on the tournament's games (settled,
  non-void). Entrants who placed no bets score 0. **Only entrants are scored.**
- **`split_pot(pot_cents: int, n_winners: int) -> PotSplit`** where
  `PotSplit(per_winner_cents, remainder_cents)`, `per = pot_cents // n_winners`,
  `remainder = pot_cents - per * n_winners`. Integer math only — no floats. Guards `n_winners >= 1`.
- **`determine_winners(scores: Mapping[int, int]) -> Winners`** — the set of telegram_ids tied at the
  max score and that max score. Empty input → no winners.
- **`compute_outcome(entrant_scores, pot_cents) -> TournamentOutcome`** — combines the two: winners,
  winning score, `per_winner_cents`, `remainder_cents`. If there are entrants but every score ties
  (including all-zero), all entrants are winners and split.

Money **formatting** lives in the display layer (`text_pt`), not the domain:
`format_money_cents(cents, currency, decimals=2) -> "R$ 3,33"` (pt-BR comma decimal). The remainder is
rendered as `(sobra R$ 0,01)` and omitted when zero.

`tournament_currency` is a new global `config.yaml` setting (default `"R$"`); optional
`tournament_currency_decimals` (default 2).

---

## 7. End trigger, announcement & corrections

`tournament_service.on_game_settled(session, fixture_id) -> list[TournamentAnnouncement]` runs after
**both** settlement paths settle a fixture (`poll_job._settle_and_announce` and the reconcile job).

For each tournament containing `fixture_id`:
1. If **not** all its games are settled-or-void → skip (wait for the rest).
2. Compute entrant standings and the pot (`entrants × entry_price_cents`) and the outcome.
3. **No scorable result** → emit a `🏁 Torneio "<nome>" encerrado — sem resultado` notice (no winner):
   - **All games void** → set `CANCELLED` (nothing was ever scorable).
   - **Valid games but zero entrants** → set `FINISHED` (the games happened; nobody entered; pot = 0).
4. Otherwise, if **unannounced** → emit the winner announcement, set `FINISHED`,
   `result_announced_at`, and `result_signature`.
5. If **already announced** and the recomputed `result_signature` differs (a §9.5 re-grade changed the
   standings) → emit a `⚠️ Resultado do torneio corrigido` correction (new winner(s)/payout),
   incrementing `correction_count`; once the cap is reached, re-grade silently and DM the admin
   (mirrors the §9.5 per-game correction cap).

The bot layer (poll/reconcile jobs) takes the returned announcements and posts them to
`group_chat_id`, best-effort (failure logs + DMs the admin, never crashes — §14).

**Winner announcement (HTML, pt-BR), example:**
```
🏆 Torneio "Oitavas de final" encerrado!
Pote: R$ 60,00 (6 entradas × R$ 10,00)

🥇 Vencedor: <a href="tg://user?id=…">Ana</a> — 14 pts
Prêmio: R$ 60,00
```
Tie example:
```
🏆 Torneio "Oitavas de final" encerrado!
Pote: R$ 60,00 (6 entradas × R$ 10,00)

🥇 Empate (3) — 12 pts cada:
• <a…>Ana</a>  • <a…>Bruno</a>  • <a…>Caio</a>
Prêmio: R$ 20,00 cada
```
Non-even split shows `R$ 20,00 cada (sobra R$ 0,01)`.

Winners are **@-mentioned** (HTML `tg://user?id=…`). Non-winning entrants need not be mentioned in the
result post (keeps pings minimal — consistent with §9.4's reasoning), but the standings line counts
are accurate.

---

## 8. Reminder integration (§9.3, merged — decision 7)

Extend `reminder_job._run_reminder` and `domain/text_pt.py::reminder_text`:

- For each game in the due reminder slot that belongs to ≥1 `OPEN` tournament, append a `🏆` block:
  - Names the tournament(s) the game is in.
  - **@-mentions the union of entrants** across those tournaments (HTML inline mention).
  - Flags entrants who have **not** placed any bet on that game yet (the "you should bet" nudge),
    e.g. `🏆 Vale pelo torneio "Oitavas"! Ainda sem palpite: <a…>Bruno</a>, <a…>Caio</a> — corre!`.
- Same-kickoff games are still combined into one message (existing behavior); the tournament block is
  per-game within that message.
- **Eligibility fix:** tournament games must be reminder-eligible even if the morning `announced_at`
  gate didn't catch them (e.g. a tournament created same-day). The reminder selection is widened to
  include due tournament games regardless of `announced_at`, still deduped by `reminded_at`.

This is the only change to the reminder's *trigger*; non-tournament reminders are unchanged.

---

## 9. Admin CLI parity (§13)

Add a `tournament` Typer sub-app in `cli.py`:
- `create`, `list`, `show <id>`, `add-game <id> <fixture_id>`, `remove-game <id> <fixture_id>`,
  `set-price <id> <preço>`, `cancel <id>` (confirmation flag), `entries <id>`,
  `recompute <id>` (rebuild standings/outcome from settled bets), `announce <id>`
  (force/re-emit the result — idempotent).

CLI output is readable tables; destructive commands require a confirmation flag (§13).

---

## 10. Documentation & maintenance (enforced by CLAUDE.md)

The same change set MUST:
- Add **COMPLETION.md §22 — Feature 7: Tournaments** (commands, lifecycle, scoring, money/split rule,
  reminder behavior, CLI), and a §21 change-log entry.
- Update the **`/ajuda`** text (and §11) to cover tournaments.
- Update **§17 (rules summary)** with the tournament rules.
- Update **§13 (CLI)** with the tournament sub-app.
- Update **§4.2 settings** + **`config.example.yaml`** with `tournament_currency`.
- Update **§19 assumptions** to record the deliberate exception: tournament setup commands are exposed
  to the group (the only bot-exposed "admin-ish" commands; everything else stays CLI-only).
- Register the new commands/scopes in `bot/app.py`.
- Add a **PROGRESS.md M12 — Tournaments** milestone block, ticked as built.
- Update **README** (§15.1) player/admin guide with tournament commands.

---

## 11. Testing strategy

- **Domain (`domain/tournament.py`), ~100% coverage:** `split_pot` (even split; remainder; single
  winner; `pot < n` e.g. `10 // 3`; zero pot); `determine_winners` (single, tie, all-zero, empty);
  `compute_outcome` end-to-end.
- **Money formatting:** `format_money_cents` (whole, decimal, remainder, configurable currency/decimals).
- **Repositories:** tournament CRUD; M:N membership; unique entry constraint; standings query sums
  `points_awarded` correctly and excludes void games/non-entrants.
- **`tournament_service`:** lock-after-first-kickoff (add/remove/price rejected); open preconditions;
  `on_game_settled` fires only when all games settled; idempotent (no double announce);
  correction on a §9.5 re-grade that flips the winner; all-void → "sem resultado"; zero entrants.
- **Bot flows (thin, `FakeProvider`):** create → add games → open → join → reminder mentions →
  settle all → winner post; `callback_data` encode/decode round-trip stays ≤ 64 bytes for the pickers
  and management card.

---

## 12. Out of scope / defaults

- **No "leave tournament" command** (YAGNI). Admin can remove an entry via CLI if needed.
- **No real payment integration** — bookkeeping only.
- **No per-tournament currency** — one global currency.
- Non-entrants who bet on tournament games still count toward the normal board, just not the tournament.
- A game voided after lock simply shrinks the tournament's scored slate.
- Single entrant → wins the whole pot (own stake back). All-zero scores → everyone ties and splits.
