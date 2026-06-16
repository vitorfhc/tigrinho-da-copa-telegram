# `/placar_jogos` (combined scoreboard for a set of ended games) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/placar_jogos` command that lets a user multi-select from the last 10 ended games and posts one combined ranking summing each player's points across the chosen games.

**Architecture:** Reuse the pure `tigrinho.scoreboard.rank()` core (it aggregates a flat list of `BetRecord`s by player). Selection state is fully stateless — encoded as a **bitmask over the position** in the last-10-ended list, packed into `callback_data` (≤64 bytes). An inline picker toggles bits (editing the same message); a compute button renders the combined board. Pure DB read, no provider call. Mirrors the existing `/placar_jogo` (single-game) flow.

**Tech Stack:** Python 3.12, python-telegram-bot 22.x (stateless `CallbackQueryHandler` + inline keyboards), SQLAlchemy 2.0 (sync), pytest + pytest-asyncio. Gates: `ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest` — all run via `uv run`.

**Conventions for every task:**
- Work in the worktree at `.claude/worktrees/placar-jogos` (branch `worktree-placar-jogos`). Run all commands from there.
- Run tests with `uv run pytest …`.
- **Before each commit, run all four gates and confirm green:**
  ```bash
  uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
  ```
- Spec reference: `docs/superpowers/specs/2026-06-16-placar-jogos-design.md`. The `§11` maintenance rule requires `/ajuda` **and** `COMPLETION.md` to change in the same commit as any command change (Task 6 handles this).

---

### Task 1: Codec — `GamesBoardToggle` + `GamesBoardCompute`

**Files:**
- Modify: `tigrinho/bot/callbacks.py`
- Test: `tests/test_callbacks.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_callbacks.py`, add `GamesBoardCompute` and `GamesBoardToggle` to the existing import block from `tigrinho.bot.callbacks` (keep alphabetical-ish order with the others), then add these four entries to the end of the `_CASES` list (before the closing `]`):

```python
    GamesBoardToggle(0, 0),
    GamesBoardToggle(1023, 9),
    GamesBoardCompute(0),
    GamesBoardCompute(1023),
```

And add these malformed strings to the `test_decode_rejects_malformed` parametrize list:

```python
    "pjt:1",
    "pjt:x:0",
    "pjt:1:x",
    "pjc:x",
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_callbacks.py -q`
Expected: FAIL with `ImportError` / `NameError` for `GamesBoardToggle` / `GamesBoardCompute`.

- [ ] **Step 3: Implement the codec arms**

In `tigrinho/bot/callbacks.py`:

(a) Add to the module docstring opcode list (after the `gb:` line):

```
  ``pjt:<mask>:<index>``              combined board picker: toggle game ``index`` (mask = selection)
  ``pjc:<mask>``                      combined board picker: compute the board for ``mask`` (§10)
```

(b) Add two frozen dataclasses (after the `GameBoard` dataclass):

```python
@dataclass(frozen=True, slots=True)
class GamesBoardToggle:
    mask: int
    index: int


@dataclass(frozen=True, slots=True)
class GamesBoardCompute:
    mask: int
```

(c) Add both to the `CallbackData` union (append before the closing paren):

```python
    | GamesBoardToggle
    | GamesBoardCompute
```

(d) Add `encode` arms (immediately before the `case _:` exhaustiveness guard):

```python
        case GamesBoardToggle(mask, index):
            result = f"pjt:{mask}:{index}"
        case GamesBoardCompute(mask):
            result = f"pjc:{mask}"
```

(e) Add `decode` arms (immediately before the final `raise ValueError(... unknown ...)`):

```python
        if op == "pjt":
            return GamesBoardToggle(int(parts[1]), int(parts[2]))
        if op == "pjc":
            return GamesBoardCompute(int(parts[1]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_callbacks.py -q`
Expected: PASS (round-trip, ≤64-byte, and malformed cases all green).

- [ ] **Step 5: Run all gates, then commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
git add tigrinho/bot/callbacks.py tests/test_callbacks.py
git commit -m "feat: callback_data codec for /placar_jogos multi-select (pjt/pjc)"
```

---

### Task 2: Keyboard — `combined_games_keyboard`

**Files:**
- Modify: `tigrinho/bot/keyboards.py`
- Test: `tests/test_keyboards.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_keyboards.py`, add `GamesBoardCompute` and `GamesBoardToggle` to the import from `tigrinho.bot.callbacks`, and `combined_games_keyboard` to the import from `tigrinho.bot.keyboards`. Then add:

```python
def test_combined_games_keyboard_toggles_and_compute() -> None:
    keyboard = combined_games_keyboard(["A x B", "C x D"], mask=0b10)
    row0 = keyboard.inline_keyboard[0][0]
    assert row0.text == "☐ A x B"
    assert decode(row0.callback_data) == GamesBoardToggle(0b10, 0)
    row1 = keyboard.inline_keyboard[1][0]
    assert row1.text == "✅ C x D"
    assert decode(row1.callback_data) == GamesBoardToggle(0b10, 1)
    compute = keyboard.inline_keyboard[2][0]
    assert compute.text == "✅ Calcular placar (1)"
    assert decode(compute.callback_data) == GamesBoardCompute(0b10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_keyboards.py::test_combined_games_keyboard_toggles_and_compute -q`
Expected: FAIL with `ImportError` for `combined_games_keyboard`.

- [ ] **Step 3: Implement the keyboard builder**

In `tigrinho/bot/keyboards.py`, add `GamesBoardCompute` and `GamesBoardToggle` to the `from tigrinho.bot.callbacks import (...)` block, then add this function (next to `ended_games_keyboard`):

```python
def combined_games_keyboard(labels: Sequence[str], mask: int) -> InlineKeyboardMarkup:
    """Multi-select picker for /placar_jogos. ``labels`` in position order; ``mask`` = selected bits."""
    rows = [
        [_button(f"{'✅' if mask & (1 << i) else '☐'} {label}", GamesBoardToggle(mask, i))]
        for i, label in enumerate(labels)
    ]
    rows.append([_button(f"✅ Calcular placar ({mask.bit_count()})", GamesBoardCompute(mask))])
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_keyboards.py::test_combined_games_keyboard_toggles_and_compute -q`
Expected: PASS.

- [ ] **Step 5: Run all gates, then commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
git add tigrinho/bot/keyboards.py tests/test_keyboards.py
git commit -m "feat: combined_games_keyboard (multi-select toggles + compute button)"
```

---

### Task 3: Board data — `load_games_records`

**Files:**
- Modify: `tigrinho/board_data.py`
- Test: `tests/test_board_data.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_board_data.py`:

```python
"""Tests for the combined-board record loader (COMPLETION.md §10)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from tigrinho.board_data import load_games_records
from tigrinho.db.models import Game, GameStatus, Stage, utcnow
from tigrinho.db.repositories import BetRepository, PlayerRepository


def _seed(
    session: Session, *, fixture_id: int, status: GameStatus, telegram_id: int, name: str, points: int
) -> None:
    kickoff = datetime(2026, 6, 16, 12, 0)
    session.add(
        Game(
            fixture_id=fixture_id,
            match_hash=f"h{fixture_id}",
            stage=Stage.GROUP,
            home_team_id=10,
            home_team_name="A",
            away_team_id=20,
            away_team_name="B",
            kickoff_utc=kickoff,
            kickoff_local=kickoff,
            status=status,
            home_goals_90=1,
            away_goals_90=0,
            settled_at=utcnow(),
        )
    )
    PlayerRepository(session).get_or_create(telegram_id, name)
    bet = BetRepository(session).upsert(
        fixture_id=fixture_id, player_telegram_id=telegram_id, category="WINNER", payload_json="{}"
    )
    bet.points_awarded = points
    bet.is_correct = points > 0
    bet.settled_at = utcnow()
    session.flush()


def test_load_games_records_sums_player_across_games(session: Session) -> None:
    _seed(session, fixture_id=1, status=GameStatus.FINISHED, telegram_id=42, name="Ana", points=5)
    _seed(session, fixture_id=2, status=GameStatus.FINISHED, telegram_id=42, name="Ana", points=2)
    records = load_games_records(session, [1, 2])
    assert len(records) == 2
    assert sum(r.points for r in records if r.telegram_id == 42) == 7


def test_load_games_records_excludes_voided_game(session: Session) -> None:
    _seed(session, fixture_id=1, status=GameStatus.FINISHED, telegram_id=42, name="Ana", points=5)
    _seed(session, fixture_id=2, status=GameStatus.VOID, telegram_id=42, name="Ana", points=2)
    records = load_games_records(session, [1, 2])
    assert len(records) == 1
    assert records[0].points == 5
```

(The `session` fixture is provided by `tests/conftest.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_board_data.py -q`
Expected: FAIL with `ImportError` for `load_games_records`.

- [ ] **Step 3: Implement the loader**

In `tigrinho/board_data.py`, add `from collections.abc import Sequence` (top of the imports) and this function at the end of the file:

```python
def load_games_records(session: Session, fixture_ids: Sequence[int]) -> list[BetRecord]:
    """Project several finished games' settled bets into one record list (combined board, §10).

    Delegates to :func:`load_game_records` per fixture (so ``VOID`` and unsettled bets are skipped),
    then concatenates; :func:`tigrinho.scoreboard.rank` sums each player across the set.
    """
    records: list[BetRecord] = []
    for fixture_id in fixture_ids:
        records.extend(load_game_records(session, fixture_id))
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_board_data.py -q`
Expected: PASS (both tests).

- [ ] **Step 5: Run all gates, then commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
git add tigrinho/board_data.py tests/test_board_data.py
git commit -m "feat: load_games_records — combined settled-bet records across a set of games"
```

---

### Task 4: Text — `games_board_text`

**Files:**
- Modify: `tigrinho/domain/text_pt.py`
- Test: `tests/test_text_pt.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_text_pt.py`, add `games_board_text` to the import from `tigrinho.domain.text_pt`, then add:

```python
def test_games_board_text_combines_games_and_medals() -> None:
    text = games_board_text(
        games=[("França", "Espanha", 0, 0), ("Japão", "Coreia", 1, 0)],
        rows=[(1, "Ana", 7), (2, "Bruno", 4), (3, "Caio", 2)],
    )
    assert "Placar — 2 jogos" in text
    assert "• França 0x0 Espanha" in text
    assert "• Japão 1x0 Coreia" in text
    assert "🥇 Ana — <b>7</b> pts" in text
    assert "🥈 Bruno" in text
    assert "🥉 Caio" in text


def test_games_board_text_singular_and_escapes() -> None:
    text = games_board_text(games=[("A & B", "C", 2, 1)], rows=[(1, "Z", 5)])
    assert "Placar — 1 jogo" in text
    assert "A &amp; B 2x1 C" in text


def test_games_board_text_no_bettors() -> None:
    text = games_board_text(games=[("A", "B", 1, 0)], rows=[])
    assert "Ninguém apostou nesses jogos" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_text_pt.py -k games_board_text -q`
Expected: FAIL with `ImportError` for `games_board_text`.

- [ ] **Step 3: Implement the text builder**

In `tigrinho/domain/text_pt.py`, add this function directly after `game_board_text` (it reuses the existing module-level `_MEDALS` and the imported `escape` / `Sequence`):

```python
def games_board_text(
    *,
    games: Sequence[tuple[str, str, int | None, int | None]],
    rows: Sequence[tuple[int, str, int]],
) -> str:
    """Combined scoreboard over a set of ended games (§10).

    ``games``: ``(home, away, home_goals_90, away_goals_90)`` for each selected game (header lines).
    ``rows``: ``(rank, name, points)`` summed across those games (same tie-breaks as /placar).
    """
    count = len(games)
    title = f"🏆 <b>Placar — {count} {'jogo' if count == 1 else 'jogos'}</b>"
    game_lines = []
    for home, away, home_goals, away_goals in games:
        score = (
            f" {home_goals}x{away_goals} "
            if home_goals is not None and away_goals is not None
            else " x "
        )
        game_lines.append(f"• {escape(home)}{score}{escape(away)}")
    header = "\n".join([title, *game_lines])
    if not rows:
        return f"{header}\n\nNinguém apostou nesses jogos. 🙈"
    lines = [header, ""]
    for position, name, points in rows:
        marker = _MEDALS.get(position, f"{position}.")
        lines.append(f"{marker} {escape(name)} — <b>{points}</b> pts")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_text_pt.py -k games_board_text -q`
Expected: PASS (all three).

- [ ] **Step 5: Run all gates, then commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
git add tigrinho/domain/text_pt.py tests/test_text_pt.py
git commit -m "feat: games_board_text — combined-board pt-BR message (N jogos + medals)"
```

---

### Task 5: Handlers — `/placar_jogos` command + toggle + compute

**Files:**
- Modify: `tigrinho/bot/board_handlers.py`
- Test: `tests/test_board_handlers.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_board_handlers.py`:

(a) Extend the existing imports:
- from `tigrinho.bot.board_handlers` add `games_board_compute`, `games_board_toggle`, `placar_jogos_handler`.
- from `tigrinho.bot.callbacks` add `GamesBoardCompute`, `GamesBoardToggle`.

(b) Append these tests at the end of the file (they reuse the existing `_seed_finished_game_with_bets`, `_cmd_update`, `_context`, `_callback_update` helpers):

```python
# --- /placar_jogos (combined scoreboard for a set of ended games) ---------------------------


async def test_placar_jogos_lists_picker(app_context: AppContext) -> None:
    _seed_finished_game_with_bets(app_context)
    update, message = _cmd_update()
    await placar_jogos_handler(update, _context(app_context))
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    assert isinstance(keyboard, InlineKeyboardMarkup)
    toggle = keyboard.inline_keyboard[0][0]
    assert toggle.text == "☐ Brasil 2 x 1 Argentina"
    assert decode(toggle.callback_data) == GamesBoardToggle(0, 0)
    compute = keyboard.inline_keyboard[-1][0]
    assert compute.text == "✅ Calcular placar (0)"
    assert decode(compute.callback_data) == GamesBoardCompute(0)


async def test_placar_jogos_empty(app_context: AppContext) -> None:
    update, message = _cmd_update()
    await placar_jogos_handler(update, _context(app_context))
    assert "Nenhum jogo encerrado" in message.reply_text.await_args.args[0]


async def test_games_board_toggle_selects_game(app_context: AppContext) -> None:
    _seed_finished_game_with_bets(app_context)
    update, query = _callback_update(encode(GamesBoardToggle(0, 0)))
    await games_board_toggle(update, _context(app_context))
    query.answer.assert_awaited()
    keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
    toggle = keyboard.inline_keyboard[0][0]
    assert toggle.text == "✅ Brasil 2 x 1 Argentina"
    assert decode(toggle.callback_data) == GamesBoardToggle(1, 0)
    compute = keyboard.inline_keyboard[-1][0]
    assert compute.text == "✅ Calcular placar (1)"
    assert decode(compute.callback_data) == GamesBoardCompute(1)


async def test_games_board_compute_sums_across_games(app_context: AppContext) -> None:
    _seed_finished_game_with_bets(app_context, fixture_id=2002)
    _seed_finished_game_with_bets(app_context, fixture_id=2003)
    # Two games seeded -> mask 0b11 selects both. Alice: 8+8=16, Bob: 3+3=6.
    update, query = _callback_update(encode(GamesBoardCompute(0b11)))
    await games_board_compute(update, _context(app_context))
    query.answer.assert_awaited()
    text = query.edit_message_text.await_args.args[0]
    assert "Placar — 2 jogos" in text
    assert "🥇 Alice — <b>16</b> pts" in text
    assert "🥈 Bob — <b>6</b> pts" in text


async def test_games_board_compute_empty_selection_shows_toast(app_context: AppContext) -> None:
    _seed_finished_game_with_bets(app_context)
    update, query = _callback_update(encode(GamesBoardCompute(0)))
    await games_board_compute(update, _context(app_context))
    query.answer.assert_awaited_with("Selecione ao menos um jogo.")
    query.edit_message_text.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_board_handlers.py -k "placar_jogos or games_board" -q`
Expected: FAIL with `ImportError` for `placar_jogos_handler` / `games_board_toggle` / `games_board_compute`.

- [ ] **Step 3: Implement the handlers + registration**

In `tigrinho/bot/board_handlers.py`:

(a) Extend imports:
- `from tigrinho.board_data import load_board_records, load_game_records, load_games_records`
- `from tigrinho.bot.callbacks import (BoardScope, BoardView, GameBoard, GamesBoardCompute, GamesBoardToggle, decode)`
- `from tigrinho.bot.keyboards import board_toggle_keyboard, combined_games_keyboard, ended_games_keyboard`
- `from tigrinho.domain.text_pt import board_text, game_board_text, games_board_text`

(b) Add the limit constant next to `_ENDED_GAMES_LIMIT`:

```python
# How many recently-ended games to offer in the /placar_jogos multi-select picker.
_COMBINED_GAMES_LIMIT = 10
_COMBINED_PICKER_PROMPT = "Escolha os jogos para somar o placar (toque para marcar):"
```

(c) Add the render helpers + three handlers (place them after `game_board_select`, before `register_board_handlers`):

```python
def _render_picker(
    app_context: AppContext, mask: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Build the /placar_jogos multi-select picker for ``mask``; None when no game has ended."""
    with app_context.session_factory() as session:
        games = GameRepository(session).list_recently_ended(_COMBINED_GAMES_LIMIT)
        labels = [
            _ended_game_label(g.home_team_name, g.away_team_name, g.home_goals_90, g.away_goals_90)
            for g in games
        ]
    if not labels:
        return None
    return _COMBINED_PICKER_PROMPT, combined_games_keyboard(labels, mask)


def _render_combined_board(app_context: AppContext, mask: int) -> str | None:
    """Combined-board text for the games selected by ``mask``; None if nothing is selected."""
    with app_context.session_factory() as session:
        games = GameRepository(session).list_recently_ended(_COMBINED_GAMES_LIMIT)
        selected = [g for i, g in enumerate(games) if mask & (1 << i)]
        if not selected:
            return None
        records = load_games_records(session, [g.fixture_id for g in selected])
        rows = [(e.rank, e.display_name, e.points) for e in rank(records)]
        header_games = [
            (g.home_team_name, g.away_team_name, g.home_goals_90, g.away_goals_90) for g in selected
        ]
    return games_board_text(games=header_games, rows=rows)


async def placar_jogos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/placar_jogos — multi-select picker; sums points across the chosen ended games (§10)."""
    message = update.effective_message
    if message is None:
        return
    rendered = _render_picker(get_app_context(context.application), mask=0)
    if rendered is None:
        await message.reply_text("Nenhum jogo encerrado ainda. 🐯")
        return
    text, keyboard = rendered
    await message.reply_text(text, reply_markup=keyboard)


async def games_board_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline (^pjt:) — flip one game's selection bit and re-render the picker."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, GamesBoardToggle):
        return
    await query.answer()
    new_mask = data.mask ^ (1 << data.index)
    rendered = _render_picker(get_app_context(context.application), new_mask)
    if rendered is None:
        await safe_edit_text(query, "Nenhum jogo encerrado ainda. 🐯")
        return
    text, keyboard = rendered
    await safe_edit_text(query, text, reply_markup=keyboard)


async def games_board_compute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline (^pjc:) — render the combined board for the selected games (toast if none)."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida.")
        return
    if not isinstance(data, GamesBoardCompute):
        return
    text = _render_combined_board(get_app_context(context.application), data.mask)
    if text is None:
        await query.answer("Selecione ao menos um jogo.")
        return
    await query.answer()
    await safe_edit_text(query, text)
```

(d) In `register_board_handlers`, register the command and the two callbacks **before** the wizard catch-all (alongside the existing `^bv:`/`^gb:` patterns):

```python
    application.add_handler(CommandHandler("placar_jogos", placar_jogos_handler))
    application.add_handler(CallbackQueryHandler(games_board_toggle, pattern="^pjt:"))
    application.add_handler(CallbackQueryHandler(games_board_compute, pattern="^pjc:"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_board_handlers.py -k "placar_jogos or games_board" -q`
Expected: PASS (all five).

- [ ] **Step 5: Run all gates, then commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
git add tigrinho/bot/board_handlers.py tests/test_board_handlers.py
git commit -m "feat: /placar_jogos handlers — multi-select picker + combined board"
```

---

### Task 6: Wiring — command scopes, `/ajuda`, and `COMPLETION.md` (§11 maintenance rule)

**Files:**
- Modify: `tigrinho/bot/app.py`
- Modify: `tigrinho/domain/text_pt.py` (`help_text`)
- Modify: `COMPLETION.md`
- Test: `tests/test_text_pt.py`, `tests/test_app.py`

- [ ] **Step 1: Write the failing test updates**

(a) In `tests/test_text_pt.py`, add `"/placar_jogos"` to the `commands` tuple inside `test_help_text_covers_required_content` (right after `"/placar_jogo",`).

(b) In `tests/test_app.py`, in `test_build_application_registers_handlers`, add `"placar_jogos"` to the asserted subset so it reads:

```python
    assert {"start", "ajuda", "apostar", "minhas_apostas", "jogos", "placar_jogos"} <= command_names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_text_pt.py::test_help_text_covers_required_content tests/test_app.py::test_build_application_registers_handlers -q`
Expected: FAIL — `/placar_jogos` not in help text; `placar_jogos` not in registered commands.

- [ ] **Step 3: Implement the wiring**

(a) In `tigrinho/bot/app.py`, add a `BotCommand` to **both** lists, immediately after the existing `BotCommand("placar_jogo", ...)` line in each:

```python
    BotCommand("placar_jogos", "Placar somando vários jogos encerrados"),
```

(b) In `tigrinho/domain/text_pt.py` `help_text()`, add a line right after the `/placar_jogo` line:

```python
        "• /placar_jogos — placar somando vários jogos encerrados\n"
```

(c) In `COMPLETION.md`:
- In §10, add this bullet immediately after the `/placar_jogo` bullet (the one ending "...voided games are excluded."):

```markdown
- **`/placar_jogos`** — combined scoreboard for a **set** of already-ended games. Posts an inline
  **multi-select** picker of the last 10 ended games (most-recent first); tapping a game toggles a
  `☐`/`✅`, then **`✅ Calcular placar`** **edits the same message** to show one ranking summing
  each player's points across the selected games (same tie-break order), under a header naming the
  chosen games. Works in group and DM; derived purely from those games' settled bets (no provider
  call); voided games excluded. Selection is stateless — a **bitmask over the picker position**
  packed into `callback_data` (≤64 bytes); positions resolve against the current last-10 list on
  each callback, and the result header names exactly the games summed.
```

- On the command-scope line (currently: `` `/placar`, `/placar_jogo`, `/palpite`, `/ajuda` work in group + DM). ``), insert `` `/placar_jogos`, `` after `` `/placar_jogo`, ``.
- On the module-map line for `board_handlers.py` (currently mentions `/placar` + `/placar_jogo`), append `+ /placar_jogos (combined multi-game board)`.
- Append a dated decision note in the decisions log (bottom of the file), e.g.:

```markdown
### 2026-06-16 — Feature: combined scoreboard for a set of ended games (`/placar_jogos`, §10)

User request. New `/placar_jogos` (group + DM): inline **multi-select** picker over the last 10
ended games; tapping toggles `☐`/`✅` (editing the same message), then `✅ Calcular placar` edits
to one ranking summing each player's points across the selected games (reuses `scoreboard.rank()`,
same tie-breaks). Pure DB read; voided games excluded.
- Selection is **stateless**: a bitmask over the picker position packed into `callback_data`
  (`pjt:<mask>:<index>` toggle, `pjc:<mask>` compute; ≤64 bytes). Positions resolve against the
  current last-10 list each callback; the result header names exactly the games summed (accepted
  list-drift caveat — encoding fixture ids cannot fit 64 bytes).
- `callbacks.GamesBoardToggle`/`GamesBoardCompute` (+ round-trip/malformed tests);
  `keyboards.combined_games_keyboard`; `board_data.load_games_records`; `text_pt.games_board_text`;
  `board_handlers.placar_jogos_handler` + `games_board_toggle` (`^pjt:`) + `games_board_compute`
  (`^pjc:`), registered before the wizard catch-all.
- `/ajuda` + `app.PRIVATE/GROUP_COMMANDS` gained `/placar_jogos`; COMPLETION.md §10 + command-scope
  list updated (§11 maintenance rule). Design + plan under `docs/superpowers/`.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_text_pt.py::test_help_text_covers_required_content tests/test_app.py::test_build_application_registers_handlers -q`
Expected: PASS.

- [ ] **Step 5: Run all gates, then commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
git add tigrinho/bot/app.py tigrinho/domain/text_pt.py COMPLETION.md tests/test_text_pt.py tests/test_app.py
git commit -m "feat: register /placar_jogos + update /ajuda and COMPLETION.md (§11)"
```

---

### Task 7: Final verification

- [ ] **Step 1: Full gate sweep**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict . && uv run pytest -q
```
Expected: all green; pytest passes (baseline was 343; this plan adds ~14 tests → ~357), domain coverage still 100%.

- [ ] **Step 2: Sanity-check the new flow against the spec**

Confirm by reading the diff that: the picker lists the last 10 ended games as toggles; toggling edits the same message; compute sums across selected games; empty selection shows a toast; `/ajuda` + `COMPLETION.md` mention `/placar_jogos`. No provider/budget calls were introduced (grep the new code for `provider`/`budget` → none).

- [ ] **Step 3: Update `PROGRESS.md`**

Append a one-line note under the decisions log mirroring the COMPLETION.md decision note (the loop convention), then:

```bash
git add PROGRESS.md
git commit -m "docs: record /placar_jogos in PROGRESS.md"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Multi-select picker → Task 2 (keyboard) + Task 5 (command/toggle). ✅
- Last-10 pool → `_COMBINED_GAMES_LIMIT = 10` in Task 5. ✅
- Combined totals via `rank()` → Task 3 (`load_games_records`) + Task 5 (`_render_combined_board`). ✅
- Header naming selected games → Task 4 (`games_board_text`). ✅
- Stateless position bitmask in `callback_data` → Task 1 (`pjt`/`pjc`). ✅
- Group + DM, no provider call → Task 5 (pure DB reads), Task 6 (both command scopes). ✅
- Empty pool / empty selection / malformed callback / no-bettors edge cases → Tasks 4 & 5. ✅
- `/ajuda` + `COMPLETION.md` (§11) → Task 6. ✅

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to" — every code step shows full code. ✅

**Type consistency:** `GamesBoardToggle(mask, index)` and `GamesBoardCompute(mask)` field names/order are identical across callbacks.py, keyboards.py, tests, and handlers. `combined_games_keyboard(labels: Sequence[str], mask: int)`, `load_games_records(session, fixture_ids)`, and `games_board_text(*, games, rows)` signatures match every call site. ✅
