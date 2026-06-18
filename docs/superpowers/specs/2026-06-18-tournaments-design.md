# Bolãozinhos (Tournaments) — Design Spec

**Date:** 2026-06-18
**Status:** Approved for planning (Revision 2 — review fixes folded in)
**Feature:** Feature 7 — Bolãozinhos (becomes COMPLETION.md §22)

> **Naming.** User-facing the feature is a **"bolãozinho"** (diminutive of *bolão da copa*) — all slash
> commands and pt-BR messages use that word. **Internal code keeps English identifiers**
> (`tournament*` tables, `Tournament` models, `tournament_service.py`), consistent with the codebase
> convention of English code + pt-BR UI (e.g. the `Bet` model behind `/apostar`). Wherever this doc
> says "tournament" it means the internal entity; the UI says "bolãozinho".

> **Revision 2 — what changed from the multi-POV review.** This revision incorporates the fixes the
> user requested after the adversarial spec review:
> - **F18** — game picker no longer uses a position bitmask; it is **identity-based and writes membership
>   immediately** (no position drift).
> - **F11** — only the **creator** (or the configured admin) may manage a bolãozinho.
> - **F4 / F13 / F5** — end-of-bolãozinho evaluation now fires from **every** game state change
>   (settle, **void**, **un-void/reschedule**) plus a **periodic sweep** that also locks, drains stuck
>   games, and rescues stranded bolãozinhos; terminal states are revivable.
> - **F8** — a member game stays **reconcile-eligible for the bolãozinho's whole lifetime**, and any
>   re-grade re-evaluates and corrects the bolãozinho regardless of the per-game reconcile window.
> - **F17** — reminder mentions are **capped, deduped, and never block the base reminder** (mark-reminded
>   even on a permanent oversized-message failure).
> - **F12** — entry **price locks at the first entry** (uniform price for everyone; no retroactive billing).
> - **F10** — a single terminal state (`CANCELLED`) for "no scorable result".
> - **Join window** — closes at the **first game's kickoff** (no late joins).
> - **Money model** — the winner does **not** win their own stake back: prize = pot − one entry; ties
>   split that prize.
> - **`/bolaozinho_criar`** rejects names containing `|`.

---

## 1. Summary

A **bolãozinho** is a competition created inside the group over a fixed set of World Cup fixtures with a
real-money **entry price**. Players **enter** with `/entrar`; the **pot** = (number of entrants × entry
price). When every game in the bolãozinho has finished and been graded, the bot posts a group message
naming the **winner(s)** and the **prize**. The winner is whoever has the most points across the
bolãozinho's games (using the existing bet points, 5/2/2/2/1).

**Money model (the winner keeps their own stake):** the **prize** is the pot **minus one entry** — i.e.
the winner collects everyone else's money but not their own. With 10 players at R$10 the pot is R$100
and the prize is **R$90**. If *k* players tie on points, they split that prize: **R$90 ÷ k** each, with
any leftover cent shown as a "sobra". A lone entrant's prize is **R$0** (just their own stake).

The bot is **bookkeeping only** — it never moves real money. It tracks entrants, computes the pot/prize,
and announces who won how much; the friends settle offline.

### Product decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Concurrency / membership | **Multiple bolãozinhos can be active at once; a game may belong to many** (many-to-many). |
| 2 | Join window | **Open from publish until the bolãozinho's first game kicks off** *(Rev 2: was "until all games end")*. |
| 3 | Money & currency | **Bookkeeping only**; **one global currency** in `config.yaml`. |
| 4 | Prize & rounding | **Prize = pot − one entry**, split among tied winners, **2-decimal with remainder shown** *(Rev 2: was "split the whole pot")*. |
| 5 | Who can manage | **Anyone can create**; only the **creator (or admin)** can modify/open/cancel a given bolãozinho *(Rev 2: tightened from "anyone")*. Commands work in group + DM. |
| 6 | Scoring & ties | **Reuse existing bet points**; **pure points-equality** decides winners (NO board tie-breaks). |
| 7 | 1h-before notification | **Merged into the existing §9.3 reminder**, adding capped entrant @-mentions. |
| 8 | When to announce winner | **When all member games are resolved** (settled-or-void), from any trigger; corrected on later re-grades. |

---

## 2. Architecture & integration points

This feature layers onto the completed M0–M11 build and adds a new **M12 — Bolãozinhos** milestone in
`PROGRESS.md`. It reuses, and must stay consistent with, these existing surfaces:

- **Settlement** flows through `tigrinho/settlement_service.py::settle_fixture`, called by **both**
  `bot/poll_job.py::_settle_and_announce` and the reconcile job (`bot/reconcile_job.py`, §9.5).
- **VOID / un-void / reschedule** of a game happen **only in `bot/sync_job.py`** (§9.1). *(Rev 2: the
  bolãozinho end-trigger must hook here too — this was the root of F4/F5/F13.)*
- **Standings** derive purely from `bets.points_awarded` — no denormalized per-entrant score is stored.
- **The 1h reminder** lives in `bot/reminder_job.py::_run_reminder` + `domain/text_pt.py::reminder_text`.
- **Commands** register in `bot/app.py` (`build_application`, `PRIVATE_COMMANDS`, `GROUP_COMMANDS`,
  `set_commands`).
- **Settings** are pydantic-validated in `tigrinho/config.py`.
- **Money is never a float.** Entry price, pot, and prize are integer **minor units (cents)** everywhere;
  only the display layer renders a decimal string.

### The single resolution hook

All "is this bolãozinho done / did its standings change?" logic lives in one Telegram-agnostic function,
**`tournament_service.on_game_resolved(session, fixture_id) -> list[TournamentAnnouncement]`**
*(Rev 2: renamed from `on_game_settled`; it fires on settle **and** void **and** un-void/reschedule).*
It is called from **every** place a member game's state can change:

| Caller | Event |
|---|---|
| `poll_job._settle_and_announce` | a game finished & was graded |
| `reconcile_job` (§9.5) | a settled game was re-graded |
| `sync_job` | a game became **VOID** (postponed/cancelled) |
| `sync_job` | a VOID game was **un-voided** / **rescheduled** back to SCHEDULED |
| `bolaozinho_sweep` job (new) | periodic backstop (lock, drain stuck, rescue stranded) |

### New / changed modules

| Module | Purpose |
|---|---|
| `tigrinho/domain/tournament.py` | **Pure** scoring, winner selection, pot/prize math (~100% line+branch coverage). No I/O. |
| `tigrinho/db/models.py` | Add `Tournament`, `TournamentGame`, `TournamentEntry` models. |
| `tigrinho/db/migrations/versions/<rev>_add_tournaments.py` | New append-only Alembic migration. |
| `tigrinho/db/repositories.py` | `TournamentRepository` (CRUD, membership, entries, standings, lock/resolve queries). |
| `tigrinho/tournament_service.py` | Auth checks, lock logic, pot/prize, `on_game_resolved`, outcome signature/correction. Shared by bot + CLI. |
| `tigrinho/bot/tournament_handlers.py` | Slash commands + identity-based inline pickers/cards (stateless `callback_data`). |
| `tigrinho/bot/sweep_job.py` (new) | `bolaozinho_sweep`: persist first-kickoff lock, drain stuck/far-postponed games, rescue stranded bolãozinhos. |
| `tigrinho/bot/keyboards.py`, `bot/callbacks.py` | Tournament pickers/cards + opcodes (payloads ≤ 64 bytes). |
| `tigrinho/domain/text_pt.py` | Tournament renderers + money formatting; extend `reminder_text` (capped mentions). |
| `tigrinho/bot/poll_job.py`, `reconcile_job.py`, `sync_job.py` | Call `on_game_resolved` and post announcements/corrections. |
| `tigrinho/bot/reminder_job.py` | Merge capped entrant mentions into the reminder. |
| `tigrinho/cli.py` | `bolaozinho` Typer sub-app (CLI parity, §13). |
| `tigrinho/config.py`, `config.example.yaml` | `tournament_currency`, `reminder_max_mentions`, sweep cadence settings. |

---

## 3. Data model

One new append-only Alembic migration. Datetimes are naive UTC (`utcnow()`); money is integer cents.

### `tournaments`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK autoincrement | The handle used in commands. |
| `name` | TEXT | Human label; **MUST NOT contain `|`** (enforced at create). |
| `entry_price_cents` | INTEGER, `> 0` | Money in minor units of `tournament_currency`. **Frozen once the first entry exists** (F12). |
| `status` | TEXT enum | `DRAFT` \| `OPEN` \| `FINISHED` \| `CANCELLED`. |
| `created_by` | BIGINT | telegram_id of the creator — **authoritative for permissions** (F11). |
| `created_at` | TIMESTAMP | UTC. |
| `opened_at` | TIMESTAMP NULL | Set on publish (`OPEN`). |
| `locked_at` | TIMESTAMP NULL | **Persisted, one-way** lock set when the first member game kicks off (F12). Freezes games, price, and joins; a later reschedule never clears it. |
| `result_announced_at` | TIMESTAMP NULL | Idempotency for the winner post. |
| `result_signature` | TEXT NULL | Stable hash of the announced outcome (winner ids + per-winner cents + remainder), to detect a re-grade flipping the result (F8). |
| `correction_count` | INTEGER default 0 | Caps oscillating corrections (mirrors §9.5). |

### `tournament_games` (M:N)
`(tournament_id FK, fixture_id FK)`, PK = the pair. A game may be in many bolãozinhos.

### `tournament_entries`
`id` PK; `tournament_id` FK; `player_telegram_id` FK (joining auto-creates the `Player`); `joined_at`;
**UNIQUE(`tournament_id`, `player_telegram_id`)**. *(Rev 2: no per-entry price column — price is uniform
because it freezes at the first entry, so `pot = count(entries) × entry_price_cents`.)*

> Cascade: deleting a bolãozinho (creator/admin/CLI) cascades to its games + entries. Games are never
> row-deleted in normal operation — a postponed/cancelled fixture is set to `status = VOID` (§9.1), so
> its `tournament_games` row persists and the void game is excluded from scoring.

---

## 4. Lifecycle, permissions & locking

```
DRAFT ──/bolaozinho_abrir──▶ OPEN ──(first game kicks off: locked_at set)──▶ OPEN+locked
  │                            │                                                  │
  │                            │                          (all member games resolved)
  │                            │                                                  ▼
  └──── ❌ Cancelar ───────────┴──────────────────────────────────────▶ FINISHED / CANCELLED
                                                                              │
                              (a member game un-voids / re-grades) ──────────┘  ← revivable (F5)
```

- **`DRAFT`** — created by `/bolaozinho_criar`. Games + price editable. Not joinable, not announced.
- **`OPEN`** — published via `/bolaozinho_abrir`. Joinable until `locked_at`.
- **First-kickoff lock (`locked_at`, persisted, one-way)** — when the earliest member game reaches its
  kickoff, the bolãozinho **freezes**: no add/remove games, no price change, **no new entries** (F1/F12).
  Set by the `bolaozinho_sweep` job and opportunistically by any join/edit attempt that observes kickoff
  has passed. A later reschedule of that game does **not** unlock.
- **`FINISHED`** — set when all member games are resolved (settled-or-void) and the winner post emitted.
- **`CANCELLED`** — creator/admin cancel, **or** auto when there is **no scorable result** (all member
  games void **or** zero entrants) — a single terminal state for both cases (F10). Per §7 a member game
  later coming back to life can **revive** a terminal bolãozinho (F5).

### Permissions (F11)
- **Anyone** in the group can `/bolaozinho_criar` their own bolãozinho.
- **Only its `created_by` creator, or the configured `admin_user_id`,** may add/remove games, set price,
  open, or cancel a given bolãozinho. Any other user's management action is refused with a clear pt-BR
  message (e.g. *"Só quem criou o bolãozinho pode mexer nele."*). `created_by` is authoritative — the UI
  never trusts who tapped the button alone; the handler re-checks identity.
- **Anyone** can `/entrar`, `/bolaozinhos`, `/bolaozinho <id>` (read + join are open to all).

### Invariants (enforced in `tournament_service`, clear pt-BR refusals)
- A game is addable only if `status == SCHEDULED` and `kickoff_utc > now`.
- No add/remove/price-change once `locked_at` is set (or kickoff has passed → set `locked_at` first).
- Price is editable only while **zero entries exist** *and* not locked (F12) — once anyone joins it is frozen.
- `/bolaozinho_abrir` requires `DRAFT`, price set (`> 0`), ≥ 1 game, and no game started yet.
- Entries accepted only while `status == OPEN` and `locked_at IS NULL` (F1).
- `result_announced_at` makes the winner post idempotent.

---

## 5. Commands (pt-BR)

Read/join commands are open to everyone; management commands are creator/admin-only (§4). All work in
group + DM, registered with the right scopes. Inline pickers/cards use stateless `callback_data`
(≤ 64 bytes) decoded in `bot/callbacks.py`. Every management callback **re-verifies the actor is the
creator/admin** before acting.

### Management (creator/admin only)
- **`/bolaozinho_criar <nome> | <preço>`** — create a `DRAFT`. The argument **MUST contain exactly one
  `|`** (F19): split into name (left, trimmed, non-empty, **no `|`**) and price (right). Price parses a
  decimal in `tournament_currency` (`10`, `10,50`, `10.50`) to cents; reject missing/zero/negative/
  unparseable with a usage hint. Replies with a **management card** (game count, price, status, buttons
  `➕ Adicionar jogos`, `📣 Abrir`, `❌ Cancelar`).
- **`➕ Adicionar jogos`** — opens an **identity-based** multi-select picker of upcoming (`SCHEDULED`,
  future) games (most-imminent first, ~10). *(Rev 2, F18:)* each toggle's `callback_data` carries the
  **`fixture_id`** (not a list position) and **writes membership to `tournament_games` immediately**
  (`☐`↔`✅` = remove/add); the picker re-renders from the DB, so there is **no position bitmask and no
  drift**. Each toggle re-validates the fixture is `SCHEDULED` + future + bolãozinho unlocked. `✅ Pronto`
  just closes the card. (This unifies add/remove — a separate remove command is unnecessary.)
- **`/bolaozinho_preco <id> <preço>`** — set/adjust price; allowed only while zero entries and unlocked.
- **`/bolaozinho_abrir <id>`** (or `📣 Abrir`) — publish: set `OPEN`, post the group announcement
  (`🏆 Novo bolãozinho: <nome> — entrada <preço> — N jogos … — use /entrar para participar`) with one
  `🎯 Apostar` deep-link per game.
- **`❌ Cancelar`** — set `CANCELLED` (creator/admin; confirmation tap).

### Joining (anyone)
- **`/entrar`** — lists `OPEN`, still-joinable bolãozinhos (or, if exactly one, jumps to it). Tapping
  shows the games + kickoffs, entry price, **current pot/prize**, entrant count, and `✅ Entrar (<preço>)`.
  Confirming creates the entry (auto-creating the `Player`, freezing the price if first entry), then shows
  **which games to bet on** with `🎯 Apostar` deep-links. Re-entering is a friendly no-op. Joining is
  refused once locked (*"As entradas fecharam — o primeiro jogo já começou."*).

### Views (anyone, group + DM)
- **`/bolaozinhos`** — list bolãozinhos with status, pot/prize, entrant count; tap to open details.
- **`/bolaozinho <id>`** — details: games (with kickoffs/results), price, pot, prize, entrants, a **live
  mini-standings** among entrants (from settled bets so far), and the caller's own entered/bet status.

`/ajuda` MUST gain a bolãozinho section (how it works, the commands, the prize = pot − one entry / split
rule, "bets still close at kickoff", "joining closes at the first game"). Per the maintenance rule the
same change updates COMPLETION.md.

---

## 6. Scoring, winner & money — pure domain (`domain/tournament.py`)

Pure, deterministic, no I/O — target ~100% line+branch coverage (same bar as `scoring.py`).

- **Entrant score** = Σ `points_awarded` over that entrant's bets on the bolãozinho's games (settled,
  non-void). Entrants who placed no bets score 0. **Only entrants are scored.**
- **Pot** `pot_cents = n_entrants × entry_price_cents` (uniform price guaranteed by the first-entry lock).
- **Prize** `prize_cents = max(0, pot_cents − entry_price_cents) = (n_entrants − 1) × entry_price_cents`
  *(Rev 2: the winner does not win their own stake; a lone entrant's prize is 0).*
- **`determine_winners(scores: Mapping[int, int]) -> Winners`** — the set of telegram_ids tied at the
  max score, plus that max score. Empty input → no winners. *(Pure equality; no board tie-breaks.)*
- **`split_prize(prize_cents: int, n_winners: int) -> PrizeSplit`** where
  `PrizeSplit(per_winner_cents, remainder_cents)`, `per = prize_cents // n_winners`,
  `remainder = prize_cents − per × n_winners`. Integer math only; guards `n_winners >= 1`.
- **`compute_outcome(entrant_scores, entry_price_cents) -> TournamentOutcome`** — combines the above:
  pot, prize, winners, winning score, `per_winner_cents`, `remainder_cents`. If every entrant ties
  (incl. all-zero), all are winners and split the prize.

> **Worked examples (n entrants, price p):** n=10, p=R$10 → pot R$100, prize R$90; 1 winner → R$90;
> 2 winners → R$45 each; 3 winners → R$30 each; 7 winners → R$12,85 cada (sobra R$0,05). n=1 → prize R$0.
> *(Note: for multi-winner ties this splits the single-winner prize, per the user's `90 ÷ k` rule.)*

Money **formatting** lives in `text_pt` (not the domain): `format_money_cents(cents, currency,
decimals=2) -> "R$ 90,00"` (pt-BR comma). The remainder renders as `(sobra R$ 0,05)`, omitted when zero.
`tournament_currency` (default `"R$"`) and optional `tournament_currency_decimals` (default 2) are global
`config.yaml` settings.

---

## 7. End trigger, announcement & corrections

`on_game_resolved(session, fixture_id)` runs after **every** member-game state change (the table in §2)
and on the periodic sweep. For each bolãozinho containing `fixture_id`:

1. If **not** all its games are resolved (every game `FINISHED` or `VOID`) → skip (wait for the rest).
2. Compute standings, pot, prize, outcome.
3. **No scorable result** (all games void **or** zero entrants) → set `CANCELLED` and emit
   `🏁 Bolãozinho "<nome>" encerrado — sem resultado` (F10: one terminal state).
4. **Unannounced** → emit the winner announcement, set `FINISHED`, `result_announced_at`,
   `result_signature`.
5. **Already terminal but inputs changed** *(Rev 2, F5/F8):*
   - If `result_signature` now differs (a re-grade or a resurrected game changed standings) → emit a
     `⚠️ Resultado do bolãozinho corrigido` correction (new winner(s)/prize), `correction_count += 1`.
     Past the cap → re-grade silently + DM admin (mirrors §9.5).
   - If a previously `CANCELLED` (all-void) bolãozinho now has a real scorable game (un-void/reschedule)
     → it **revives**: recompute and, when all games are again resolved, announce as in step 4.

**Late-correction reach (F8).** A member game stays **reconcile-eligible for the bolãozinho's whole
lifetime** (until `FINISHED`), not just the per-game `reconcile_window_hours` — so a late VAR correction
to an *early* game in a multi-day bolãozinho is still pulled and routed through `on_game_resolved`. This
widening is budget-guarded and bounded by the bolãozinho ending.

**Stuck / stranded games (F4/F13).** The `bolaozinho_sweep` job is the backstop:
- Sets the persisted `locked_at` when the first member game's kickoff passes.
- If all member games are resolved but the bolãozinho was never announced (e.g. the **last game was
  VOIDed in sync_job** — F4) → calls `on_game_resolved` to finish it.
- If a member game is past `kickoff + match_window_hours` and still unsettled, or postponed beyond the
  fetch window and stuck `SCHEDULED` (F13) → DMs the admin a **bolãozinho-aware** escalation
  (*"Bolãozinho <nome> travado no jogo #<id> — pode precisar de settle/cancel manual via CLI."*) so the
  pot is never silently stranded.

The bot layer posts the returned announcements to `group_chat_id`, best-effort (failure logs + DMs admin,
never crashes — §14).

**Winner announcement (HTML, pt-BR), examples:**
```
🏆 Bolãozinho "Oitavas" encerrado!
Pote: R$ 100,00 (10 entradas × R$ 10,00) · Prêmio: R$ 90,00

🥇 Vencedor: <a href="tg://user?id=…">Ana</a> — 14 pts
Leva R$ 90,00
```
Tie:
```
🏆 Bolãozinho "Oitavas" encerrado!
Pote: R$ 100,00 · Prêmio: R$ 90,00

🥇 Empate (2) — 12 pts cada:
• <a…>Ana</a>  • <a…>Bruno</a>
Cada um leva R$ 45,00
```
Winners are @-mentioned; non-winning entrants need not be (minimal pings, per §9.4's reasoning).

---

## 8. Reminder integration (§9.3, merged — decision 7, F17)

Extend `reminder_job._run_reminder` + `text_pt.reminder_text`:

- For each game in the due slot belonging to ≥ 1 `OPEN` bolãozinho, append a `🏆` block naming the
  bolãozinho(s) and **@-mentioning entrants who have not yet bet on that game** ("ainda sem palpite —
  corre!").
- **Mentions are deduped across overlapping bolãozinhos and capped** at `reminder_max_mentions` (default
  ~20); beyond the cap the block shows `… +N`. *(Rev 2, F17: prevents oversized messages.)*
- **The tournament block must never break the base reminder.** If a send still fails permanently
  (oversized/entity limit), the sweep **still marks the slot reminded** (or sends a trimmed fallback)
  instead of returning un-marked and retrying the same oversized message every cycle. Transient failures
  retry as today.
- Tournament games are reminder-eligible even if the morning `announced_at` gate didn't catch them
  (same-day creation); still deduped by `reminded_at`.
- Joining closes at the first kickoff (§4), so the entrant set a reminder pings is bounded.

This is the only change to the reminder's trigger; non-tournament reminders are unchanged.

---

## 9. Admin CLI parity (§13)

A `bolaozinho` Typer sub-app in `cli.py`: `create`, `list`, `show <id>`, `add-game <id> <fixture_id>`,
`remove-game <id> <fixture_id>`, `set-price <id> <preço>`, `cancel <id>` (confirm flag),
`entries <id>`, `add-entry`/`remove-entry <id> <telegram_id>` (admin fixups), `recompute <id>`
(rebuild standings/outcome from settled bets), `announce <id>` (force/re-emit — idempotent),
`sweep` (run the lock/stuck/rescue pass once). Output is readable tables; destructive commands require a
confirmation flag.

---

## 10. Documentation & maintenance (enforced by CLAUDE.md)

The same change set MUST: add **COMPLETION.md §22 — Feature 7: Bolãozinhos** + a §21 change-log entry;
update **`/ajuda`** (and §11); update **§17 (rules summary)**; update **§13 (CLI)**; add
`tournament_currency`, `reminder_max_mentions`, and sweep settings to **§4.2 + `config.example.yaml`**;
update **§19 assumptions** (record the deliberate exception that bolãozinho create/read/join are
group-exposed while management is creator/admin-only); register the new commands/scopes in `bot/app.py`;
add a **PROGRESS.md M12 — Bolãozinhos** block; update the **README** (§15.1) player/admin guide.

---

## 11. Testing strategy

- **Domain (`domain/tournament.py`), ~100% coverage:** `prize_cents` (n=1 → 0; (n−1)·p); `split_prize`
  (even; remainder; single winner; `prize < n_winners`; zero prize); `determine_winners` (single, tie,
  all-zero, empty); `compute_outcome` end-to-end with the §6 worked examples.
- **Money formatting:** `format_money_cents` (whole, decimal, remainder, configurable currency/decimals).
- **Repositories:** CRUD; M:N membership; unique entry; **price freeze at first entry**; standings query
  sums `points_awarded`, excludes void games + non-entrants + ungraded/NULL points.
- **`tournament_service`:** **creator/admin-only** management refusals (F11); **price-lock at first
  entry** (F12); **persisted one-way `locked_at`** unaffected by reschedule (F12); **join refused after
  first kickoff** (F1); `on_game_resolved` fires from settle/void/un-void; **last-game-VOID finishes the
  bolãozinho** (F4); **stuck/far-postponed escalation** (F13); **un-void revives a CANCELLED bolãozinho**
  (F5); **late re-grade beyond the per-game window still corrects** (F8); idempotent announce; one
  terminal state for no-result (F10).
- **Picker (F18):** identity-based toggle writes the exact fixture the user saw even when the candidate
  list re-orders/shrinks between taps (no position drift).
- **Bot flows (thin, `FakeProvider`):** create → add games → open → join (price freezes) → reminder
  mentions (capped) → settle all → winner post; `callback_data` round-trip ≤ 64 bytes for pickers/cards.
- **Reminder (F17):** mention dedup + cap; a permanently-oversized send still marks the slot reminded.

---

## 12. Out of scope / defaults

- **No "leave bolãozinho" command** for players (YAGNI); admin/creator can remove an entry via CLI.
- **No real payment integration** — bookkeeping only.
- **No per-bolãozinho currency** — one global currency.
- Non-entrants who bet on member games still count toward the normal board, just not the bolãozinho.
- A game voided after lock simply shrinks the scored slate (and can revive a finished bolãozinho if it
  later un-voids — §7).
- **Lone entrant** → prize R$0 (own stake back). **All-zero scores** → everyone ties and splits the prize.
- **Multi-winner tie math** uses the user's `prize ÷ k` rule (splits the single-winner prize); strict
  "losers' money only" division was considered and not adopted.
