# Design — slimmer `/minhas_apostas` (summarized settled history)

Status: approved (brainstorm 2026-06-17)
Spec for: `COMPLETION.md` §8.2 (`/minhas_apostas` listing command)

## Goal

`/minhas_apostas` (DM) currently dumps **every** bet into three sections — "Em aberto",
"Em andamento", "Encerrados" — with the settled section listing one line per settled bet.

The World Cup 2026 has **104 matches × up to 5 categories ≈ 520 bets per player**. Open and
in-progress bets are inherently bounded (only games that haven't been graded yet), but the
**settled history grows without bound** and will overflow Telegram's 4096-char message limit
well before the knockout stage.

This redesign keeps the actionable/live sections in full and replaces the unbounded settled
dump with a **one-line summary + an on-demand, paginated drill-down**, so the default message
stays short no matter how far the tournament has progressed.

Non-goals: changing how bets are placed, graded, or scored; changing the scoreboard. Pure
presentation + querying change in the bet-listing surface.

## Approach

Three views, navigated by **editing one message in place** (the same pattern the wizard and
the scoreboard already use). All three are DM-only and always rendered for the **calling user**
(`update.effective_user.id`); no user id is ever read from `callback_data`.

1. **Default view** — sent by the `/minhas_apostas` command. Shows "Em aberto" and
   "Em andamento" exactly as today (bounded), and collapses "Encerrados" into a single summary
   line plus a `📜 Ver encerrados (N)` button when settled bets exist.
2. **History view** — paginated, most-recent-first, **one button per game** (~8/page), reached
   from the default view. `Voltar` returns to the default view.
3. **Per-game detail view** — the calling player's own per-category breakdown for one finished
   game (✓/✗ + points each). `Voltar` returns to the history page it came from.

### Why these three decisions (the non-obvious ones)

- **Summarize only "Encerrados", not the whole command.** Open bets are bounded by how many
  games are open for betting at once, and in-progress by how many games are live at once — both
  small. Only settled history is unbounded, so only it needs taming. Keeping open/live in full
  preserves the "what can I still change / what's live right now" glance value.
- **One line per game, not per bet.** Aggregating a game's up-to-5 category bets into a single
  line (`Brasil 2×1 Croácia · 3✓1✗ +12 pts`) caps the history at ~104 rows instead of ~520,
  and games are the unit players actually remember.
- **In-place message edit, not new messages.** Matches the existing wizard/board patterns and
  keeps the DM uncluttered. The default → history → detail navigation forms a small stack, each
  step re-rendered from the DB (stateless; survives restarts).
- **`Voltar` from detail must land on the originating history page**, so the page index is
  carried in the detail opcode.

### Flow

```
/minhas_apostas (command) ──► Default view (new message)
  • Em aberto … (full)          buttons: 🗑 per open bet
  • Em andamento … (full)                + [📜 Ver encerrados (N)]  → mh:0
  • 📜 Encerrados: N · A✓ B✗ · +P pts

[📜 Ver encerrados] ──► History page p (edit)
  header: 📜 Seus encerrados — página p+1/T
  buttons: one per game on the page  → mg:<fixture>:<p>
           [◀ Anterior] [Voltar] [Próxima ▶]   (Anterior/Próxima omitted at ends)
  [Voltar] → mm (back to default view)

[game button] ──► Per-game detail (edit)
  header: <home> <hg>×<ag> <away>
  • <category>: <prediction> — ✓/✗ +k pts   (one per stored bet)
  Total: +P pts
  button: [◀ Voltar] → mh:<p>
```

## Components (all following existing patterns)

### `tigrinho/bot/callbacks.py`

Three new frozen dataclasses + `encode`/`decode` cases, added to the `CallbackData` union. All
≤ 64 bytes (numeric ids only). Document the opcodes in the module docstring opcode table.

- `MyHistory(page: int)` → `mh:<page>` — open/navigate the settled-history page.
- `MyGameDetail(fixture_id: int, page: int)` → `mg:<fixture>:<page>` — the player's own bets for
  one finished game; `page` is the history page to return to.
- `MyBetsHome()` → `mm` — back to the default `/minhas_apostas` view.

Add round-trip coverage in `tests/test_callbacks.py` (or wherever the codec is tested),
including the byte-length guard.

### `tigrinho/db/repositories.py` (`BetRepository`)

New methods so the handler neither loads ~500 rows nor does an N+1 game lookup:

- `settled_summary_for_player(telegram_id) -> SettledSummary` — one aggregate over the player's
  settled bets (`settled_at IS NOT NULL`): `count`, `correct` (count of `is_correct`),
  `points` (sum of `points_awarded`), and `game_count` (`COUNT(DISTINCT fixture_id)`, used for
  the page total). `SettledSummary` is a small frozen dataclass / NamedTuple.
- `settled_games_for_player(telegram_id, *, limit, offset) -> list[SettledGameRow]` — GROUP BY
  `fixture_id` over the player's settled bets, joined to `games`, ordered by
  `games.settled_at DESC` (most-recent-first), with `LIMIT/OFFSET`. Each row carries:
  `fixture_id`, `home_team_name`, `away_team_name`, `home_goals_90`, `away_goals_90`,
  `bet_count`, `correct_count`, `points`. `SettledGameRow` is a small frozen dataclass.

`total_pages = ceil(summary.game_count / PAGE_SIZE)`. Per-game detail reuses the existing
`list_for_player_and_game(telegram_id, fixture_id)`.

### `tigrinho/domain/text_pt.py`

Pure, unit-testable rendering functions (no I/O), consistent with existing `*_text` helpers:

- `settled_summary_line(count, correct, points) -> str` — e.g.
  `📜 <b>Encerrados</b>: 42 palpites · 30✓ 12✗ · +87 pts`. Wrong = `count - correct`.
- `my_history_header(page, total_pages) -> str` — e.g. `📜 <b>Seus encerrados</b> — página 2/6`.
- `my_history_game_label(row) -> str` — the per-game **button** label, e.g.
  `Brasil 2×1 Croácia · 3✓1✗ +12 pts` (plain text, no HTML — button labels are not parsed).
- `my_game_detail_text(home, away, home_goals, away_goals, lines) -> str` — header + one line per
  stored bet (`• <category>: <prediction> — ✓/✗ +k pts`) + `Total: +P pts`. The per-line
  prediction text reuses `describe_bet` (via the existing `_describe_stored` helper in the
  handler) so wording stays identical to the wizard/listing.

### `tigrinho/bot/keyboards.py`

- `my_bets_keyboard(open_bets, *, settled_count)` — extend the existing helper (or add a sibling)
  so it appends a `📜 Ver encerrados ({settled_count})` button (→ `MyHistory(0)`) below the
  per-open-bet 🗑 rows when `settled_count > 0`.
- `my_history_keyboard(rows, page, total_pages)` — one button per game
  (label `my_history_game_label(row)`, → `MyGameDetail(fixture_id, page)`), then a nav row:
  `[◀ Anterior]`(→ `MyHistory(page-1)`, omitted on first page), `[Voltar]`(→ `MyBetsHome()`),
  `[Próxima ▶]`(→ `MyHistory(page+1)`, omitted on last page).
- `my_game_detail_keyboard(page)` — single `[◀ Voltar]` button (→ `MyHistory(page)`).

### `tigrinho/bot/bets_handlers.py`

- `minhas_apostas_handler` — drop the `settled_lines` accumulation; instead compute the summary
  via `settled_summary_for_player`. Still render "Em aberto" / "Em andamento" in full. Build the
  keyboard with the new `settled_count`. Refactor the default-view body into a helper
  (`_render_my_bets_default(session, telegram_id) -> (text, keyboard)`) so both the command and
  the `MyBetsHome` callback render identically.
- Add cases to the **existing catch-all `on_callback` dispatcher** (no new handler registration
  needed — `on_callback` is the unpatterned `CallbackQueryHandler`):
  - `MyHistory(page)` → `_show_history_page(query, app_context, user.id, page)`
  - `MyGameDetail(fixture_id, page)` → `_show_game_detail(query, app_context, user.id, fixture_id, page)`
  - `MyBetsHome()` → re-render the default view (`_render_my_bets_default`) and `_edit`.
- Page clamping: clamp the requested `page` into `[0, total_pages-1]` before querying, so stale
  buttons (more games settled since the message was sent) never produce an empty/negative page.

### `tigrinho/bot/app.py`, `/ajuda`, `COMPLETION.md` (maintenance rule, §11)

- No new handler registration in `app.py` (catch-all `on_callback` already covers the opcodes).
- Update the **`/ajuda`** text (in `text_pt.py`) describing `/minhas_apostas` to mention the
  summarized history + drill-down.
- Update **`COMPLETION.md` §8.2**'s `/minhas_apostas` bullet to describe: open/live shown in full,
  settled collapsed to a summary line, and the paginated per-game history with own-bet detail.

## Data flow

1. `/minhas_apostas` → `minhas_apostas_handler` opens a session, lists the caller's bets, splits
   open vs in-progress (via `_is_open` / `settled_at`), and reads the settled aggregate. Renders
   the default view as a **new** message with `my_bets_keyboard(..., settled_count=N)`.
2. `📜 Ver encerrados` tap → `MyHistory(0)` → `on_callback` → `_show_history_page`: clamp page,
   `settled_games_for_player(limit=PAGE_SIZE, offset=page*PAGE_SIZE)`, build header + keyboard,
   **edit** the message.
3. Game button tap → `MyGameDetail(fixture, page)` → `_show_game_detail`:
   `list_for_player_and_game(user.id, fixture)`, render own-bet breakdown, **edit** the message.
4. `Voltar` (detail) → `MyHistory(page)`; `Voltar` (history) → `MyBetsHome` → re-render default.

`PAGE_SIZE = 8` (module constant). Ordering is `games.settled_at DESC`.

## Error handling / edge cases

- **No bets at all** → unchanged "Você ainda não fez nenhum palpite. Use /apostar! 🐯".
- **Settled bets but no open/live** → default view is just the summary line + `Ver encerrados`.
- **Open/live but no settled** → no summary line, no `Ver encerrados` button (settled_count = 0).
- **Stale page index** (more games settled after the message was sent) → clamp to valid range;
  never render a negative/empty page. If somehow zero settled games, `MyHistory` falls back to
  re-rendering the default view.
- **Detail for a fixture the player has no (settled) bet in** (crafted/stale callback) → since
  `list_for_player_and_game` is scoped to the caller, it returns empty; render a short
  "Palpite não encontrado." and a `Voltar` to page 0. Never leaks another user's data.
- **Game still in progress / not graded** must not appear in history (query filters on settled
  bets only) — those stay in "Em andamento" on the default view.
- All taps `await query.answer()` (already done centrally in `on_callback`).
- Message-size: default ≤ open(bounded) + live(bounded) + 1 line; history = ≤8 short buttons +
  header; detail = 1 game, ≤5 lines. All comfortably under 4096 chars.

## Testing

- **Codec** (`tests/test_callbacks.py`): round-trip `MyHistory`, `MyGameDetail`, `MyBetsHome`;
  byte-length under 64.
- **Repository** (`tests/test_repositories*.py`): `settled_summary_for_player` counts/sums and
  `game_count`; `settled_games_for_player` ordering (recent-first), pagination (limit/offset
  boundaries), per-game aggregation (count/correct/points), and exclusion of open/ungraded bets.
- **Text** (`tests/test_text_pt.py`): `settled_summary_line` (incl. wrong = count − correct,
  zero state), `my_history_header`, `my_history_game_label`, `my_game_detail_text` (✓/✗, total).
- **Handlers** (`tests/test_bets_handlers.py`): default view summary line + `Ver encerrados`
  button presence/absence by settled_count; `MyHistory` page navigation incl. clamp on stale
  page; `MyGameDetail` shows only the caller's bets and `Voltar` carries the page; `MyBetsHome`
  re-renders the default; empty/edge states above.
- All four gates green (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`).
  Domain (`scoring.py`/`settlement.py`) is untouched, so its ~100% coverage is unaffected; the
  new `text_pt.py` helpers are pure and fully covered.

## Out of scope (YAGNI)

- Paginating "Em aberto" / "Em andamento" — bounded by open/live games; the same pattern can be
  applied later if they ever bloat.
- Changing the 🗑-delete → "🗑 Palpite apagado." confirmation flow (no `Voltar` after delete).
- Accuracy percentage in the summary line, filtering history by stage/outcome, or search — all
  deferrable; the count·✓·✗·points summary is enough to start.
- Reusing the group-wide per-game board (`gb:`) for detail — detail intentionally shows the
  caller's **own** picks, not the whole group's ranking.
