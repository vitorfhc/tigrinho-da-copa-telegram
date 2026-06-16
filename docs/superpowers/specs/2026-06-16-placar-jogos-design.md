# Design — `/placar_jogos` (combined scoreboard for a set of ended games)

**Date:** 2026-06-16
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Spec home:** extends `COMPLETION.md` §10 (Feature 3 — Scoreboard) and the §3/§12 command-scope list.

## Goal

A new command that computes **one combined scoreboard summing each player's points across a
*chosen set* of already-ended games**. It is the multi-game generalization of the existing
`/placar_jogo` (single ended game). Example: pick two finished games and see who scored the most
points across just those two.

Clarified semantics (chosen with the user via clarifying questions):

- **Selection UX:** an **inline multi-select picker** — the bot lists ended games as toggle
  buttons; tapping one flips a `☐`/`✅` check (editing the same message); a final
  **`✅ Calcular placar (N)`** button computes the combined board.
- **Game pool:** the **last 10 ended games** (most-recently-settled first). No pagination.
- **Output:** **combined totals only** — one ranking summing each player's points over the
  selected games, under a header naming the selected games. No per-game breakdown.
- **Command:** `/placar_jogos` (plural sibling of `/placar_jogo`), usable in the **group and in
  DM**, like the other board commands. Pure DB read — **no provider call**.
- **Voided games never participate** (they are not `FINISHED`+settled, so they never appear in the
  pool, and the per-game loader already skips `VOID`).

## Approach

Reuse the existing pure ranking core. `tigrinho.scoreboard.rank()` already aggregates a flat list of
`BetRecord`s **by player**, applying the four tie-breaks (points desc, exact-score hits desc, total
correct desc, earliest `created_at`). So the combined board is simply: **collect the settled-bet
records for every selected game, concatenate them, and call `rank()` once** — it sums each player's
points across the set for free. This mirrors `/placar_jogo`, which calls `rank()` on one game's
records.

### Stateless selection via a position bitmask (the one non-obvious decision)

Selection state lives **entirely in `callback_data`**, matching the codebase's deliberate
stateless-wizard pattern (no `user_data`/`chat_data`; see the M6 decision in `PROGRESS.md`). Telegram
caps `callback_data` at **64 bytes**, so we cannot pack a set of fixture IDs (up to 10 × ~7-digit IDs
≫ 64 bytes). Instead we encode the selection as a **bitmask over the game's *position* in the
last-10-ended list**: bit *i* set ⇒ the *i*-th game is selected. The mask's max value is
2¹⁰−1 = 1023, so a toggle payload like `pjt:768:9` is ~10 bytes — far under the limit.

- On **every** callback we re-fetch `list_recently_ended(10)` and interpret the mask against **that
  current list**, so the rendered checkmarks always reflect the live list, and the computed board
  resolves the mask against the live list too.
- **Accepted limitation — list drift.** Positions are relative to the last-10 list at callback time.
  If a *new* game ends between opening the picker and pressing Calcular (rare: a selection session is
  seconds; World Cup games end hours apart), the list shifts by one and a checked bit can point at a
  neighbouring game (and the 10th-oldest drops off the pool). This is **transparent, not silently
  wrong**: the result message's header **names every game that was actually summed**
  (`• França 0x0 Espanha`), so the user always sees exactly what was computed. Encoding fixture IDs
  to avoid this is impossible inside 64 bytes, so the bitmask-over-positions is the only stateless
  option. We document the caveat and accept it at one-group scale.
- **Index/length guard:** when interpreting a mask, ignore any bit ≥ the current list length (defends
  against drift shrinking the pool and against a hand-crafted oversized mask).

### Flow

1. `/placar_jogos` → fetch `list_recently_ended(10)`.
   - Empty pool → reply `"Nenhum jogo encerrado ainda. 🐯"` (same copy as `/placar_jogo`).
   - Otherwise → reply with the multi-select picker, initial mask **0** (nothing selected).
2. Tapping a game button (`^pjt:<mask>:<index>`) → flip bit `index`, re-fetch the list, **edit the
   same message** to re-render the picker with updated checkmarks and the `(N)` count.
3. Tapping `✅ Calcular placar (N)` (`^pjc:<mask>`) →
   - mask resolves to **0 games** (nothing selected, or all bits dropped by the length guard) →
     `query.answer("Selecione ao menos um jogo.")` toast, leave the picker as-is.
   - otherwise → load the combined records for the selected fixtures, `rank()`, and **edit the
     message** to the combined-board text.

A single selected game is allowed (it then equals `/placar_jogo` for that game).

## Components (all following existing patterns)

### `tigrinho/bot/callbacks.py`
Two new frozen dataclasses + codec arms (added to the `CallbackData` union, `encode`, `decode`, and
the module docstring opcode list):

- `GamesBoardToggle(mask: int, index: int)` → `pjt:<mask>:<index>` — flip one game's bit.
- `GamesBoardCompute(mask: int)` → `pjc:<mask>` — compute the combined board.

Both encode/decode are pure `int` round-trips, like the existing `gb:` / `bv:` arms; `decode` raises
`ValueError` on malformed input (existing contract).

### `tigrinho/bot/keyboards.py`
`combined_games_keyboard(labels: Sequence[str], mask: int) -> InlineKeyboardMarkup`
where `labels` are the game labels in **position order** (position `i` ⇒ bit `i`). For each `i`:
- prefix the label with `✅ ` if bit `i` is set, else `☐ `;
- button `callback_data = GamesBoardToggle(mask=mask, index=i)` — the button carries the **current**
  mask plus its own index; the handler computes the new mask as `mask ^ (1 << index)`. (Keeps the
  toggle reversible and the keyboard builder a trivial pure function of `(labels, mask)`.)

Final row: `✅ Calcular placar (N)` → `GamesBoardCompute(mask)`, where `N` = number of set bits in
`mask`. The compute button is always rendered; the empty-selection case is guarded in the handler
with a toast, which is simpler than reshuffling the keyboard.

### `tigrinho/board_data.py`
`load_games_records(session: Session, fixture_ids: Sequence[int]) -> list[BetRecord]` — concatenate
`load_game_records(session, fid)` for each id (the existing per-game loader already skips `VOID` and
unsettled bets and projects via the shared `_record`). Returns the flat record list for `rank()`.

### `tigrinho/domain/text_pt.py`
`games_board_text(*, games: Sequence[tuple[str, str, int | None, int | None]], rows: Sequence[tuple[int, str, int]]) -> str`
— `games` is the resolved selection as `(home, away, home_goals_90, away_goals_90)`:

```
🏆 <b>Placar — N jogos</b>
• França 0x0 Espanha
• Japão 1x0 Coreia

🥇 Ana — <b>7</b> pts
🥈 Bruno — <b>4</b> pts
🥉 Caio — <b>2</b> pts
```

Reuses `_MEDALS` and `escape(...)` exactly like `game_board_text`/`board_text`. Singular header for
`N == 1` ("1 jogo"). Empty `rows` (nobody bet on any selected game) → a friendly
`"Ninguém apostou nesses jogos. 🙈"` line under the header. No top-N cap and no caller-outside line
(the audience is one small friend group, consistent with `game_board_text`).

### `tigrinho/bot/board_handlers.py`
- `placar_jogos_handler` (`CommandHandler("placar_jogos")`) — render the picker (mask 0) or the empty
  message. Reuses the existing `_ended_game_label` + `_COMBINED_GAMES_LIMIT = 10` constant.
- `games_board_toggle` (`CallbackQueryHandler(pattern="^pjt:")`) — decode (guard type), flip the bit,
  re-fetch the last-10 list, re-render the picker via `safe_edit_text`.
- `games_board_compute` (`CallbackQueryHandler(pattern="^pjc:")`) — decode (guard type), resolve the
  mask against the current list (length-guarded), toast-guard the empty case, else build + edit to
  the combined board.
- Register both new patterns in `register_board_handlers`, **before** the wizard catch-all (same as
  the `^bv:` and `^gb:` handlers — PTB stops at the first matching handler in a group).

A shared private helper `_render_picker(app_context, mask) -> tuple[str, InlineKeyboardMarkup] | None`
builds the picker text + keyboard from the current last-10 list and a mask (returns `None` when the
pool is empty), used by both the command and the toggle callback to avoid duplication.

### `tigrinho/bot/app.py`, `/ajuda`, `COMPLETION.md`
Per the §11 maintenance rule (any command change updates `/ajuda` **and** `COMPLETION.md` in the same
change):
- Add `/placar_jogos` to the private + group command-scope lists (`setMyCommands`).
- Add a `/placar_jogos` line to the `/ajuda` text (`domain/text_pt.help_text`).
- Extend `COMPLETION.md` §10 with the `/placar_jogos` description and add it to the command-scope
  list; append a dated decision note recording the stateless position-bitmask choice and the
  list-drift caveat.

## Data flow

```
/placar_jogos ─▶ GameRepository.list_recently_ended(10) ─▶ combined_games_keyboard(labels, mask=0)
   (toggle)  ─▶ decode pjt ─▶ mask ^= 1<<index ─▶ re-fetch list ─▶ re-render picker (edit message)
  (compute)  ─▶ decode pjc ─▶ resolve mask→fixture_ids (length-guarded)
                           ─▶ load_games_records(session, fixture_ids)
                           ─▶ scoreboard.rank(records)
                           ─▶ games_board_text(games=resolved, rows) ─▶ edit message
```

Everything is a pure DB read; no `RequestBudget`/provider involvement.

## Error handling / edge cases

- **No ended games:** command replies with the empty-pool message; no keyboard.
- **Empty selection on Calcular:** toast `"Selecione ao menos um jogo."`; picker left intact.
- **Malformed `callback_data`:** `decode` raises `ValueError` → `query.answer("Ação inválida.")`
  (existing pattern in `board_toggle`/`game_board_select`).
- **List drift between render and compute:** length-guarded mask; the result header names the actual
  games summed (see decision above).
- **"Message is not modified":** re-rendering an unchanged picker (e.g. double-tap) is swallowed by
  the existing `safe_edit_text`.
- **Nobody bet on the selection:** header + `"Ninguém apostou nesses jogos. 🙈"`.

## Testing

- **`tests/test_callbacks.py`** — round-trip `GamesBoardToggle` / `GamesBoardCompute` (incl. mask 0,
  mask 1023); malformed `pjt:`/`pjc:` raise `ValueError`; encoded length ≤ 64 bytes.
- **`tests/test_keyboards.py`** — `combined_games_keyboard` renders `✅`/`☐` per the mask, the
  compute button shows the correct count, and each toggle button carries the current mask + its
  index.
- **`tests/test_board_data.py`** (or the existing board-data test module) — `load_games_records`
  sums a player across multiple games, excludes a `VOID` game, includes the union of bettors, and
  ignores unsettled bets.
- **`tests/test_text_pt.py`** — `games_board_text` header (plural/singular `N jogo(s)`, one line per
  game), medals for top 3, and the empty-`rows` branch.
- **`tests/test_board_handlers.py`** — command renders the picker; empty pool → message; a toggle
  flips a bit and re-renders; compute renders the combined board; empty-mask compute shows the toast
  and does not edit.

All four gates (`ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest`) stay green;
domain coverage stays at 100% (this feature adds no `scoring.py`/`settlement.py` lines — `rank()`
lives in `scoreboard.py`, which is covered by its own tests, not the enforced-100% domain pair).

## Out of scope (YAGNI)

- Pagination / selecting beyond the last 10 ended games (user chose a fixed 10).
- Per-game point breakdown in the output (user chose totals only).
- A weekly/Geral toggle on the combined board (it is an ad-hoc subset, not a time window).
- Selecting upcoming/unsettled games (the command is for ended games only).
