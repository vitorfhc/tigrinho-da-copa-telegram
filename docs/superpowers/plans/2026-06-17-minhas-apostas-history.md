# Slimmer `/minhas_apostas` (summarized settled history) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `/minhas_apostas` from dumping every settled bet; show open/live bets in full, collapse settled history to a one-line summary, and offer a paginated per-game drill-down with the player's own per-category breakdown.

**Architecture:** Three views navigated by editing one message in place (matching the existing stateless wizard/board pattern). The default view is sent by the `/minhas_apostas` command; tapping `📜 Ver encerrados` opens a paginated history (one button per game, most-recent-first); tapping a game shows the caller's own bets for it. New compact callback opcodes (`mh`/`mg`/`mm`) are dispatched by the existing catch-all `on_callback` handler — no new handler registration. Two new aggregate queries avoid loading ~500 rows or N+1 game lookups.

**Tech Stack:** Python 3.12, python-telegram-bot 21.x (`CallbackQueryHandler`), SQLAlchemy 2.0 sync ORM (`func`, `case`), pytest + pytest-asyncio.

## Global Constraints

Copied verbatim from `CLAUDE.md` / spec — every task implicitly includes these:

- Python **3.12+**; strong typing, **`mypy --strict`**, **no `Any` in domain** (`domain/`).
- Pure domain logic — `domain/text_pt.py` is a PURE string builder (no I/O, no clock, no DB).
- **HTML parse mode everywhere** (`ParseMode.HTML`); user-supplied strings escaped via `escape(...)`.
- Inline-button **`callback_data` ≤ 64 bytes** — numeric ids + short opcodes only.
- Betting/listing is **DM-only**; views are always rendered for `update.effective_user.id` and never trust an id read from `callback_data`.
- Commands and copy are **pt-BR**.
- **Maintenance rule (§11):** any change to commands/categories/scoring/grading MUST update `help_text()` (the `/ajuda` text) **and** `COMPLETION.md` in the same change.
- All four gates MUST pass before every commit: `ruff check .`, `ruff format --check .`, `mypy --strict .`, `pytest`.
- Match existing codebase conventions: scores rendered with ASCII `x` (e.g. `2 x 1`), not `×`.

---

### Task 1: Callback opcodes `mh` / `mg` / `mm`

Add three callback types so the history navigation can round-trip through `callback_data`.

**Files:**
- Modify: `tigrinho/bot/callbacks.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Produces (consumed by Tasks 4 & 5):
  - `MyHistory(page: int)` → `"mh:<page>"`
  - `MyGameDetail(fixture_id: int, page: int)` → `"mg:<fixture>:<page>"`
  - `MyBetsHome()` → `"mm"`
  - all added to the `CallbackData` union and handled by `encode`/`decode`.

- [ ] **Step 1: Write the failing test**

In `tests/test_callbacks.py`, add the three new types to the import block from `tigrinho.bot.callbacks`:

```python
    MyBetsHome,
    MyGameDetail,
    MyHistory,
```

Append to the `_CASES` list (before the closing `]`):

```python
    MyHistory(0),
    MyHistory(5),
    MyGameDetail(123456, 0),
    MyGameDetail(999_999_999, 12),
    MyBetsHome(),
```

Add these malformed strings to the `test_decode_rejects_malformed` parametrize list:

```python
        "mh",
        "mh:x",
        "mg:1",
        "mg:1:x",
        "mg:x:0",
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_callbacks.py -q`
Expected: FAIL — `ImportError: cannot import name 'MyHistory'`.

- [ ] **Step 3: Add the dataclasses, union members, encode/decode cases, and docstring**

In `tigrinho/bot/callbacks.py`, add to the opcode table in the module docstring (after the `pv:` line):

```
  ``mh:<page>``                       /minhas_apostas: open/navigate the settled-history page
  ``mg:<fixture>:<page>``             /minhas_apostas: my own bets for one finished game (page=return)
  ``mm``                              /minhas_apostas: back to the default listing view
```

Add three dataclasses (next to the other frozen dataclasses, e.g. after `PalpiteView`):

```python
@dataclass(frozen=True, slots=True)
class MyHistory:
    page: int


@dataclass(frozen=True, slots=True)
class MyGameDetail:
    fixture_id: int
    page: int


@dataclass(frozen=True, slots=True)
class MyBetsHome:
    pass
```

Add them to the `CallbackData` union:

```python
    | PalpiteView
    | MyHistory
    | MyGameDetail
    | MyBetsHome
)
```

Add `encode` cases (inside the `match data:` block, before the `case _:` guard):

```python
        case MyHistory(page):
            result = f"mh:{page}"
        case MyGameDetail(fixture_id, page):
            result = f"mg:{fixture_id}:{page}"
        case MyBetsHome():
            result = "mm"
```

Add `decode` cases (inside the `try:`, before the final `raise`):

```python
        if op == "mh":
            return MyHistory(int(parts[1]))
        if op == "mg":
            return MyGameDetail(int(parts[1]), int(parts[2]))
        if op == "mm":
            return MyBetsHome()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_callbacks.py -q`
Expected: PASS (all round-trip, byte-length, and malformed cases green).

- [ ] **Step 5: Run gates and commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest tests/test_callbacks.py -q
git add tigrinho/bot/callbacks.py tests/test_callbacks.py
git commit -m "feat(callbacks): mh/mg/mm opcodes for /minhas_apostas history"
```

---

### Task 2: Repository aggregate queries

Add the two aggregate queries (and their result dataclasses) so the handler reads the settled summary and per-game history without loading every bet.

**Files:**
- Modify: `tigrinho/db/repositories.py`
- Test: `tests/test_repositories.py`

**Interfaces:**
- Produces (consumed by Task 5):
  - `SettledSummary(count: int, correct: int, points: int, game_count: int)` (frozen dataclass)
  - `SettledGameRow(fixture_id: int, home_team_name: str, away_team_name: str, home_goals_90: int | None, away_goals_90: int | None, bet_count: int, correct_count: int, points: int)` (frozen dataclass)
  - `BetRepository.settled_summary_for_player(player_telegram_id: int) -> SettledSummary`
  - `BetRepository.settled_games_for_player(player_telegram_id: int, *, limit: int, offset: int) -> list[SettledGameRow]` (ordered most-recently-settled first)

- [ ] **Step 1: Write the failing test**

In `tests/test_repositories.py`, add to the imports from `tigrinho.db.repositories`:

```python
    SettledGameRow,
    SettledSummary,
```

Add this helper near the top (after the existing `_seed_game`) and the tests at the end of the file:

```python
def _settled_bet(
    session: Session,
    *,
    fixture_id: int,
    player: int,
    category: str,
    is_correct: bool,
    points: int,
    settled_at: datetime,
) -> None:
    bet = BetRepository(session).upsert(
        fixture_id=fixture_id,
        player_telegram_id=player,
        category=category,
        payload_json="{}",
    )
    bet.is_correct = is_correct
    bet.points_awarded = points
    bet.settled_at = settled_at
    session.flush()


def test_settled_summary_for_player(session: Session) -> None:
    PlayerRepository(session).get_or_create(42, "Tigrão")
    g1 = _seed_game(session, 1001)
    g1.home_goals_90, g1.away_goals_90, g1.settled_at = 2, 1, datetime(2026, 6, 16, 21, 0)
    g2 = _seed_game(session, 1002)
    g2.settled_at = datetime(2026, 6, 17, 21, 0)
    _settled_bet(session, fixture_id=1001, player=42, category="WINNER", is_correct=True,
                 points=3, settled_at=datetime(2026, 6, 16, 21, 0))
    _settled_bet(session, fixture_id=1001, player=42, category="BTTS", is_correct=False,
                 points=0, settled_at=datetime(2026, 6, 16, 21, 0))
    _settled_bet(session, fixture_id=1002, player=42, category="WINNER", is_correct=True,
                 points=5, settled_at=datetime(2026, 6, 17, 21, 0))
    # an ungraded bet for another game and another player's bet must be excluded
    BetRepository(session).upsert(fixture_id=1002, player_telegram_id=42,
                                  category="OVER_UNDER", payload_json="{}")
    _settled_bet(session, fixture_id=1001, player=99, category="WINNER", is_correct=True,
                 points=3, settled_at=datetime(2026, 6, 16, 21, 0))
    session.flush()

    summary = BetRepository(session).settled_summary_for_player(42)
    assert summary == SettledSummary(count=3, correct=2, points=8, game_count=2)


def test_settled_summary_empty(session: Session) -> None:
    assert BetRepository(session).settled_summary_for_player(42) == SettledSummary(
        count=0, correct=0, points=0, game_count=0
    )


def test_settled_games_for_player_ordered_and_paginated(session: Session) -> None:
    PlayerRepository(session).get_or_create(42, "Tigrão")
    g1 = _seed_game(session, 1001)
    g1.home_goals_90, g1.away_goals_90, g1.settled_at = 2, 1, datetime(2026, 6, 16, 21, 0)
    g2 = _seed_game(session, 1002)
    g2.home_goals_90, g2.away_goals_90, g2.settled_at = 0, 0, datetime(2026, 6, 17, 21, 0)
    _settled_bet(session, fixture_id=1001, player=42, category="WINNER", is_correct=True,
                 points=3, settled_at=datetime(2026, 6, 16, 21, 0))
    _settled_bet(session, fixture_id=1001, player=42, category="BTTS", is_correct=False,
                 points=0, settled_at=datetime(2026, 6, 16, 21, 0))
    _settled_bet(session, fixture_id=1002, player=42, category="WINNER", is_correct=True,
                 points=3, settled_at=datetime(2026, 6, 17, 21, 0))
    session.flush()

    repo = BetRepository(session)
    page0 = repo.settled_games_for_player(42, limit=1, offset=0)
    assert len(page0) == 1
    assert page0[0].fixture_id == 1002  # most recently settled first
    assert page0[0].bet_count == 1 and page0[0].correct_count == 1 and page0[0].points == 3

    page1 = repo.settled_games_for_player(42, limit=1, offset=1)
    assert len(page1) == 1
    assert page1[0] == SettledGameRow(
        fixture_id=1001, home_team_name="Brasil", away_team_name="Argentina",
        home_goals_90=2, away_goals_90=1, bet_count=2, correct_count=1, points=3,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repositories.py -q`
Expected: FAIL — `ImportError: cannot import name 'SettledSummary'`.

- [ ] **Step 3: Implement the dataclasses and queries**

In `tigrinho/db/repositories.py`, extend the imports:

```python
from dataclasses import dataclass
```

and change the SQLAlchemy import line to:

```python
from sqlalchemy import case, func, select
```

Add the two dataclasses just above `class BetRepository:`:

```python
@dataclass(frozen=True, slots=True)
class SettledSummary:
    """Aggregate of a player's graded bets (for the /minhas_apostas summary line)."""

    count: int
    correct: int
    points: int
    game_count: int


@dataclass(frozen=True, slots=True)
class SettledGameRow:
    """One finished game in a player's history, with that game's bet aggregates."""

    fixture_id: int
    home_team_name: str
    away_team_name: str
    home_goals_90: int | None
    away_goals_90: int | None
    bet_count: int
    correct_count: int
    points: int
```

Add these two methods inside `BetRepository` (e.g. after `list_for_player_and_game`):

```python
    def settled_summary_for_player(self, player_telegram_id: int) -> SettledSummary:
        """One aggregate over the player's graded bets (count / correct / points / games)."""
        stmt = select(
            func.count(Bet.id),
            func.coalesce(func.sum(case((Bet.is_correct.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(Bet.points_awarded), 0),
            func.count(func.distinct(Bet.fixture_id)),
        ).where(
            Bet.player_telegram_id == player_telegram_id,
            Bet.settled_at.is_not(None),
        )
        row = self._session.execute(stmt).one()
        return SettledSummary(count=row[0], correct=row[1], points=row[2], game_count=row[3])

    def settled_games_for_player(
        self, player_telegram_id: int, *, limit: int, offset: int
    ) -> list[SettledGameRow]:
        """The player's finished games, most-recently-settled first, with per-game aggregates."""
        stmt = (
            select(
                Game.fixture_id,
                Game.home_team_name,
                Game.away_team_name,
                Game.home_goals_90,
                Game.away_goals_90,
                func.count(Bet.id),
                func.coalesce(func.sum(case((Bet.is_correct.is_(True), 1), else_=0)), 0),
                func.coalesce(func.sum(Bet.points_awarded), 0),
            )
            .join(Game, Game.fixture_id == Bet.fixture_id)
            .where(
                Bet.player_telegram_id == player_telegram_id,
                Bet.settled_at.is_not(None),
            )
            .group_by(Game.fixture_id)
            .order_by(func.max(Game.settled_at).desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            SettledGameRow(
                fixture_id=r[0],
                home_team_name=r[1],
                away_team_name=r[2],
                home_goals_90=r[3],
                away_goals_90=r[4],
                bet_count=r[5],
                correct_count=r[6],
                points=r[7],
            )
            for r in self._session.execute(stmt).all()
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_repositories.py -q`
Expected: PASS.

- [ ] **Step 5: Run gates and commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest tests/test_repositories.py -q
git add tigrinho/db/repositories.py tests/test_repositories.py
git commit -m "feat(repo): settled summary + paginated per-game history queries"
```

---

### Task 3: pt-BR renderers

Add the four pure string builders the history views need.

**Files:**
- Modify: `tigrinho/domain/text_pt.py`
- Test: `tests/test_text_pt.py`

**Interfaces:**
- Produces (consumed by Tasks 4 & 5):
  - `settled_summary_line(count: int, correct: int, points: int) -> str`
  - `my_history_header(page: int, total_pages: int) -> str` (`page` is 0-based)
  - `my_history_game_label(*, home: str, away: str, home_goals: int | None, away_goals: int | None, correct: int, wrong: int, points: int) -> str` (plain text — a button label)
  - `my_game_detail_text(*, home: str, away: str, home_goals: int | None, away_goals: int | None, lines: Sequence[tuple[str, bool | None, int]]) -> str`

- [ ] **Step 1: Write the failing test**

In `tests/test_text_pt.py`, add the four names to the import block from `tigrinho.domain.text_pt`, then add:

```python
def test_settled_summary_line() -> None:
    line = settled_summary_line(42, 30, 87)
    assert "Encerrados" in line
    assert "42 palpites" in line
    assert "30✓" in line and "12✗" in line
    assert "+87 pts" in line


def test_settled_summary_line_singular() -> None:
    assert "1 palpite ·" in settled_summary_line(1, 1, 2)


def test_my_history_header_is_one_based() -> None:
    assert my_history_header(0, 6) == "📜 <b>Seus encerrados</b> — página 1/6"
    assert my_history_header(5, 6).endswith("6/6")


def test_my_history_game_label_plain_text() -> None:
    label = my_history_game_label(
        home="Brasil", away="Croácia", home_goals=2, away_goals=1,
        correct=3, wrong=1, points=12,
    )
    assert label == "Brasil 2x1 Croácia · 3✓1✗ +12 pts"
    assert "<" not in label  # button labels are not HTML-parsed


def test_my_game_detail_text() -> None:
    text = my_game_detail_text(
        home="Brasil", away="Croácia", home_goals=2, away_goals=1,
        lines=[("Placar exato: 2x1", True, 5), ("Ambas marcam: Não", False, 0)],
    )
    assert "Brasil 2 x 1 Croácia" in text
    assert "• Placar exato: 2x1 — ✓ 5 pts" in text
    assert "• Ambas marcam: Não — ✗ 0 pts" in text
    assert "Total: +5 pts" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_text_pt.py -q`
Expected: FAIL — `ImportError: cannot import name 'settled_summary_line'`.

- [ ] **Step 3: Implement the renderers**

In `tigrinho/domain/text_pt.py`, add (e.g. after `closed_bets_text`):

```python
def settled_summary_line(count: int, correct: int, points: int) -> str:
    """One-line summary of a player's graded bets for /minhas_apostas (§8.2)."""
    wrong = count - correct
    noun = "palpite" if count == 1 else "palpites"
    return f"📜 <b>Encerrados</b>: {count} {noun} · {correct}✓ {wrong}✗ · {points:+d} pts"


def my_history_header(page: int, total_pages: int) -> str:
    """Header for a page of a player's settled-bet history. ``page`` is 0-based."""
    return f"📜 <b>Seus encerrados</b> — página {page + 1}/{total_pages}"


def my_history_game_label(
    *,
    home: str,
    away: str,
    home_goals: int | None,
    away_goals: int | None,
    correct: int,
    wrong: int,
    points: int,
) -> str:
    """Compact one-game button label (plain text; inline-button labels don't parse HTML)."""
    score = f"{home_goals}x{away_goals}" if home_goals is not None and away_goals is not None else "x"
    return f"{home} {score} {away} · {correct}✓{wrong}✗ {points:+d} pts"


def my_game_detail_text(
    *,
    home: str,
    away: str,
    home_goals: int | None,
    away_goals: int | None,
    lines: Sequence[tuple[str, bool | None, int]],
) -> str:
    """A player's own per-category breakdown for one finished game (§8.2).

    ``lines``: ``(description, is_correct, points)`` where ``description`` is the already-built
    :func:`describe_bet` string (kept unescaped, consistent with the listing/confirmation flow).
    """
    score = (
        f" {home_goals} x {away_goals} "
        if home_goals is not None and away_goals is not None
        else " x "
    )
    out = [f"🏆 <b>{escape(home)}{score}{escape(away)}</b>", ""]
    total = 0
    for description, is_correct, points in lines:
        mark = "✓" if is_correct else "✗"
        out.append(f"• {description} — {mark} {points} pts")
        total += points
    out.append("")
    out.append(f"Total: {total:+d} pts")
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_text_pt.py -q`
Expected: PASS.

- [ ] **Step 5: Run gates and commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest tests/test_text_pt.py -q
git add tigrinho/domain/text_pt.py tests/test_text_pt.py
git commit -m "feat(text): renderers for /minhas_apostas summary, history, detail"
```

---

### Task 4: Keyboards

Add the `Ver encerrados` button to the existing default keyboard and add the history/detail keyboards.

**Files:**
- Modify: `tigrinho/bot/keyboards.py`
- Test: `tests/test_keyboards.py`

**Interfaces:**
- Consumes: `MyHistory`, `MyGameDetail`, `MyBetsHome` (Task 1); `my_history_game_label` (Task 3, used by the caller, not here).
- Produces (consumed by Task 5):
  - `my_bets_keyboard(open_bets: Sequence[tuple[int, str]], *, settled_count: int = 0) -> InlineKeyboardMarkup` (extended signature)
  - `my_history_keyboard(rows: Sequence[tuple[int, str]], page: int, total_pages: int) -> InlineKeyboardMarkup`
  - `my_game_detail_keyboard(page: int) -> InlineKeyboardMarkup`

- [ ] **Step 1: Write the failing test**

In `tests/test_keyboards.py`, add to the imports from `tigrinho.bot.callbacks`: `MyBetsHome, MyGameDetail, MyHistory`, and to the imports from `tigrinho.bot.keyboards`: `my_bets_keyboard, my_game_detail_keyboard, my_history_keyboard`. Then add:

```python
def _decoded(markup: InlineKeyboardMarkup) -> list[CallbackData]:
    return [
        decode(b.callback_data)
        for row in markup.inline_keyboard
        for b in row
        if isinstance(b.callback_data, str)
    ]


def test_my_bets_keyboard_appends_history_button_when_settled() -> None:
    markup = my_bets_keyboard([(7, "Brasil x Croácia — Vencedor: Brasil")], settled_count=42)
    decoded = _decoded(markup)
    assert MyHistory(0) in decoded


def test_my_bets_keyboard_omits_history_button_when_none_settled() -> None:
    assert MyHistory(0) not in _decoded(my_bets_keyboard([(7, "x")], settled_count=0))


def test_my_history_keyboard_nav_at_first_page() -> None:
    markup = my_history_keyboard([(1001, "Jogo A"), (1002, "Jogo B")], page=0, total_pages=3)
    decoded = _decoded(markup)
    assert MyGameDetail(1001, 0) in decoded and MyGameDetail(1002, 0) in decoded
    assert MyHistory(1) in decoded  # Próxima
    assert MyBetsHome() in decoded  # Voltar
    assert MyHistory(-1) not in decoded  # no Anterior on first page


def test_my_history_keyboard_nav_at_last_page() -> None:
    decoded = _decoded(my_history_keyboard([(1001, "Jogo A")], page=2, total_pages=3))
    assert MyHistory(1) in decoded  # Anterior
    assert MyHistory(3) not in decoded  # no Próxima on last page


def test_my_game_detail_keyboard_back_carries_page() -> None:
    assert MyHistory(2) in _decoded(my_game_detail_keyboard(2))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_keyboards.py -q`
Expected: FAIL — `ImportError: cannot import name 'my_history_keyboard'`.

- [ ] **Step 3: Implement the keyboards**

In `tigrinho/bot/keyboards.py`, add to the imports from `tigrinho.bot.callbacks`:

```python
    MyBetsHome,
    MyGameDetail,
    MyHistory,
```

Replace the existing `my_bets_keyboard` with the extended version and add the two new builders:

```python
def my_bets_keyboard(
    open_bets: Sequence[tuple[int, str]], *, settled_count: int = 0
) -> InlineKeyboardMarkup:
    """🗑 Apagar per still-open bet, plus a 📜 Ver encerrados button when history exists."""
    rows = [[_button(f"🗑 Apagar: {label}", DeleteBet(bet_id))] for bet_id, label in open_bets]
    if settled_count > 0:
        rows.append([_button(f"📜 Ver encerrados ({settled_count})", MyHistory(0))])
    return InlineKeyboardMarkup(rows)


def my_history_keyboard(
    rows: Sequence[tuple[int, str]], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Paginated settled-history list: one button per game + a nav row. ``page`` is 0-based."""
    buttons = [[_button(label, MyGameDetail(fixture_id, page))] for fixture_id, label in rows]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_button("◀ Anterior", MyHistory(page - 1)))
    nav.append(_button("Voltar", MyBetsHome()))
    if page < total_pages - 1:
        nav.append(_button("Próxima ▶", MyHistory(page + 1)))
    buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


def my_game_detail_keyboard(page: int) -> InlineKeyboardMarkup:
    """Single ◀ Voltar button returning to the originating history page."""
    return InlineKeyboardMarkup([[_button("◀ Voltar", MyHistory(page))]])
```

Note: `InlineKeyboardButton` is already imported at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_keyboards.py -q`
Expected: PASS.

- [ ] **Step 5: Run gates and commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest tests/test_keyboards.py -q
git add tigrinho/bot/keyboards.py tests/test_keyboards.py
git commit -m "feat(keyboards): Ver encerrados button + history/detail keyboards"
```

---

### Task 5: Handler wiring (default view + history + detail)

Rewrite `minhas_apostas_handler` to use the summary, factor the default view into a reusable helper, and dispatch the three new opcodes.

**Files:**
- Modify: `tigrinho/bot/bets_handlers.py`
- Test: `tests/test_bets_handlers.py`

**Interfaces:**
- Consumes: Task 1 opcodes, Task 2 queries, Task 3 renderers, Task 4 keyboards.
- Produces: updated `/minhas_apostas` behavior + `on_callback` cases for `MyHistory` / `MyGameDetail` / `MyBetsHome`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_bets_handlers.py`, add to the imports from `tigrinho.bot.callbacks`: `MyBetsHome, MyGameDetail, MyHistory`. Replace the existing `test_minhas_apostas_renders_settled_bet` with the version below and add the rest:

```python
async def _seed_settled(
    app_context: AppContext, fixture_id: int, *, hg: int, ag: int, settled_iso: datetime
) -> None:
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        assert game is not None
        game.home_goals_90, game.away_goals_90, game.settled_at = hg, ag, settled_iso
        bet = BetRepository(session).upsert(
            fixture_id=fixture_id, player_telegram_id=42, category="WINNER",
            payload_json='{"sel":"HOME"}',
        )
        bet.is_correct = True
        bet.points_awarded = 2
        bet.settled_at = settled_iso
        session.commit()


async def test_minhas_apostas_summarizes_settled(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)  # not open -> settled
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        session.commit()
    await _seed_settled(app_context, 1001, hg=2, ag=1, settled_iso=datetime.now(tz=UTC).replace(tzinfo=None))

    update, message = _cmd_update()
    await minhas_apostas_handler(update, _context(app_context))
    text = message.reply_text.await_args.args[0]
    assert "Encerrados" in text and "1 palpite" in text and "+2 pts" in text
    # the per-bet "Vencedor: Brasil" line is gone from the default view
    assert "Vencedor: Brasil" not in text
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    decoded = [
        decode(b.callback_data)
        for row in markup.inline_keyboard
        for b in row
        if isinstance(b.callback_data, str)
    ]
    assert MyHistory(0) in decoded


async def test_minhas_apostas_history_page_and_detail(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        session.commit()
    await _seed_settled(app_context, 1001, hg=2, ag=1, settled_iso=datetime.now(tz=UTC).replace(tzinfo=None))

    # open the history page
    update, query = _cb_update(encode(MyHistory(0)))
    await on_callback(update, _context(app_context))
    assert "Seus encerrados" in query.edit_message_text.await_args.args[0]
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    decoded = [
        decode(b.callback_data)
        for row in markup.inline_keyboard
        for b in row
        if isinstance(b.callback_data, str)
    ]
    assert MyGameDetail(1001, 0) in decoded

    # drill into the game detail
    update, query = _cb_update(encode(MyGameDetail(1001, 0)))
    await on_callback(update, _context(app_context))
    detail = query.edit_message_text.await_args.args[0]
    assert "Brasil 2 x 1 Argentina" in detail
    assert "Total: +2 pts" in detail


async def test_minhas_apostas_history_clamps_stale_page(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        session.commit()
    await _seed_settled(app_context, 1001, hg=2, ag=1, settled_iso=datetime.now(tz=UTC).replace(tzinfo=None))
    # request a page far beyond the one that exists -> must not error, clamps to page 1/1
    update, query = _cb_update(encode(MyHistory(99)))
    await on_callback(update, _context(app_context))
    assert "página 1/1" in query.edit_message_text.await_args.args[0]


async def test_minhas_apostas_back_to_default(app_context: AppContext) -> None:
    _seed_game(app_context, started=True)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(42, "Tigrão")
        session.commit()
    await _seed_settled(app_context, 1001, hg=2, ag=1, settled_iso=datetime.now(tz=UTC).replace(tzinfo=None))
    update, query = _cb_update(encode(MyBetsHome()))
    await on_callback(update, _context(app_context))
    assert "Encerrados" in query.edit_message_text.await_args.args[0]


async def test_my_game_detail_scoped_to_caller(app_context: AppContext) -> None:
    # _USER is id 42; a detail request for a fixture they never bet in returns "não encontrado"
    _seed_game(app_context, started=True)
    with app_context.session_factory() as session:
        PlayerRepository(session).get_or_create(99, "Outro")
        bet = BetRepository(session).upsert(
            fixture_id=1001, player_telegram_id=99, category="WINNER", payload_json="{}"
        )
        bet.is_correct = True
        bet.points_awarded = 2
        bet.settled_at = datetime.now(tz=UTC).replace(tzinfo=None)
        session.commit()
    update, query = _cb_update(encode(MyGameDetail(1001, 0)))
    await on_callback(update, _context(app_context))
    assert "não encontrado" in query.edit_message_text.await_args.args[0]
```

Keep the existing `test_minhas_apostas_shows_started_ungraded_bet_as_pending` test unchanged (it still passes: ungraded → "Em andamento", no settled summary).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bets_handlers.py -q`
Expected: FAIL — new tests error/AssertionError (handler not yet updated; `MyHistory` import may also fail).

- [ ] **Step 3: Update imports in `bets_handlers.py`**

Add to the import from `tigrinho.bot.callbacks`:

```python
    MyBetsHome,
    MyGameDetail,
    MyHistory,
```

Add to the import from `tigrinho.bot.keyboards`:

```python
    my_game_detail_keyboard,
    my_history_keyboard,
```

Add to the import from `tigrinho.domain.text_pt`:

```python
    my_game_detail_text,
    my_history_game_label,
    my_history_header,
    settled_summary_line,
```

Add a module constant near `_CLOSED_MESSAGE`:

```python
_HISTORY_PAGE_SIZE = 8
```

- [ ] **Step 4: Replace `minhas_apostas_handler` with a helper + thin handler**

Replace the whole `minhas_apostas_handler` function (currently `bets_handlers.py:374-418`) with:

```python
def _render_my_bets_default(
    session: Session, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build the default /minhas_apostas view: open + in-progress in full, settled summarized."""
    games_repo = GameRepository(session)
    bets_repo = BetRepository(session)
    open_lines: list[str] = []
    pending_lines: list[str] = []
    open_buttons: list[tuple[int, str]] = []
    for bet in bets_repo.list_for_player(telegram_id):
        game = games_repo.get(bet.fixture_id)
        if game is None:
            continue
        description = _describe_stored(bet, game)
        if _is_open(game):
            open_lines.append(f"• {escape(_game_label(game))}: {description}")
            open_buttons.append((bet.id, f"{_game_label(game)} — {description}"))
        elif bet.settled_at is None:
            pending_lines.append(
                f"• {escape(_game_label(game))}: {description} — ⏳ aguardando resultado"
            )
    summary = bets_repo.settled_summary_for_player(telegram_id)
    if not open_lines and not pending_lines and summary.count == 0:
        return "Você ainda não fez nenhum palpite. Use /apostar! 🐯", None
    parts: list[str] = []
    if open_lines:
        parts.append("<b>Em aberto</b>\n" + "\n".join(open_lines))
    if pending_lines:
        parts.append("<b>Em andamento</b>\n" + "\n".join(pending_lines))
    if summary.count > 0:
        parts.append(settled_summary_line(summary.count, summary.correct, summary.points))
    keyboard = (
        my_bets_keyboard(open_buttons, settled_count=summary.count)
        if (open_buttons or summary.count > 0)
        else None
    )
    return "\n\n".join(parts), keyboard


async def minhas_apostas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/minhas_apostas (DM) — open/live in full, settled summarized + drill-down (§8.2)."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    app_context = get_app_context(context.application)
    with app_context.session_factory() as session:
        text, keyboard = _render_my_bets_default(session, user.id)
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
```

- [ ] **Step 5: Add the two callback helpers**

Add after `_delete_bet` (around `bets_handlers.py:369`):

```python
async def _show_history_page(
    query: CallbackQuery, app_context: AppContext, telegram_id: int, page: int
) -> None:
    with app_context.session_factory() as session:
        bets_repo = BetRepository(session)
        summary = bets_repo.settled_summary_for_player(telegram_id)
        if summary.game_count == 0:
            text, keyboard = _render_my_bets_default(session, telegram_id)
            await _edit(query, text, keyboard=keyboard)
            return
        total_pages = (summary.game_count + _HISTORY_PAGE_SIZE - 1) // _HISTORY_PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        button_rows = [
            (
                row.fixture_id,
                my_history_game_label(
                    home=row.home_team_name,
                    away=row.away_team_name,
                    home_goals=row.home_goals_90,
                    away_goals=row.away_goals_90,
                    correct=row.correct_count,
                    wrong=row.bet_count - row.correct_count,
                    points=row.points,
                ),
            )
            for row in bets_repo.settled_games_for_player(
                telegram_id, limit=_HISTORY_PAGE_SIZE, offset=page * _HISTORY_PAGE_SIZE
            )
        ]
    await _edit(
        query,
        my_history_header(page, total_pages),
        keyboard=my_history_keyboard(button_rows, page, total_pages),
    )


async def _show_game_detail(
    query: CallbackQuery, app_context: AppContext, telegram_id: int, fixture_id: int, page: int
) -> None:
    with app_context.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        bets = BetRepository(session).list_for_player_and_game(telegram_id, fixture_id)
        if game is None or not bets:
            await _edit(query, "Palpite não encontrado.", keyboard=my_game_detail_keyboard(page))
            return
        lines = [
            (
                _describe_stored(bet, game),
                bet.is_correct,
                bet.points_awarded if bet.points_awarded is not None else 0,
            )
            for bet in bets
        ]
        text = my_game_detail_text(
            home=game.home_team_name,
            away=game.away_team_name,
            home_goals=game.home_goals_90,
            away_goals=game.away_goals_90,
            lines=lines,
        )
    await _edit(query, text, keyboard=my_game_detail_keyboard(page))
```

- [ ] **Step 6: Dispatch the new opcodes in `on_callback`**

In the `match data:` block of `on_callback` (after the `case DeleteBet(bet_id):` arm), add:

```python
        case MyHistory(page):
            await _show_history_page(query, app_context, user.id, page)
        case MyGameDetail(fixture_id, page):
            await _show_game_detail(query, app_context, user.id, fixture_id, page)
        case MyBetsHome():
            with app_context.session_factory() as session:
                text, keyboard = _render_my_bets_default(session, user.id)
            await _edit(query, text, keyboard=keyboard)
```

- [ ] **Step 7: Run the handler tests**

Run: `pytest tests/test_bets_handlers.py -q`
Expected: PASS (new + existing tests).

- [ ] **Step 8: Run full gates and commit**

```bash
ruff check . && ruff format --check . && mypy --strict . && pytest -q
git add tigrinho/bot/bets_handlers.py tests/test_bets_handlers.py
git commit -m "feat(minhas_apostas): summarized settled history with paginated drill-down"
```

---

### Task 6: Docs — `/ajuda` + `COMPLETION.md` (maintenance rule §11)

Reflect the new `/minhas_apostas` behavior in the user-facing help and the spec.

**Files:**
- Modify: `tigrinho/domain/text_pt.py` (`help_text`)
- Modify: `COMPLETION.md` (§8.2)
- Test: `tests/test_text_pt.py` (help-text assertion if one exists)

- [ ] **Step 1: Update the `/ajuda` line**

In `tigrinho/domain/text_pt.py`, in `help_text()`, replace the `/minhas_apostas` bullet:

```python
        "• /minhas_apostas — ver seus palpites: em aberto e em andamento na hora, e os "
        "encerrados resumidos (toque em 📜 Ver encerrados para o histórico, jogo a jogo) "
        "(no privado)\n"
```

- [ ] **Step 2: Update `COMPLETION.md` §8.2**

Replace the `/minhas_apostas` bullet in §8.2 with:

```markdown
- **`/minhas_apostas`** (DM) — the caller's bets. **Em aberto** (still-changeable) and
  **Em andamento** (kicked off, ungraded) are listed in full, each open bet carrying an inline
  **🗑 Apagar** button. **Encerrados** (graded) are collapsed to a one-line summary
  (`N palpites · A✓ B✗ · ±P pts`) plus a `📜 Ver encerrados (N)` button that opens a paginated,
  most-recent-first history — one button per game (`<home> <h>x<a> <away> · A✓B✗ ±P pts`,
  `_HISTORY_PAGE_SIZE` per page). Tapping a game shows the caller's own per-category breakdown
  for it (✓/✗ + points each, with a total); navigation edits the one message in place. This keeps
  the default message bounded across all 104 fixtures. Deleting an open bet is allowed; deleting/
  editing a started game's bet is rejected.
```

- [ ] **Step 3: Verify help text still renders and gates pass**

Run: `pytest tests/test_text_pt.py -q && ruff check . && ruff format --check . && mypy --strict . && pytest -q`
Expected: PASS. (If a help-text test asserts specific substrings, update it to match the new bullet.)

- [ ] **Step 4: Update `PROGRESS.md` and commit**

Tick/append a note in `PROGRESS.md` about the `/minhas_apostas` history rework, then:

```bash
git add tigrinho/domain/text_pt.py COMPLETION.md PROGRESS.md tests/test_text_pt.py
git commit -m "docs: /ajuda + COMPLETION.md §8.2 for summarized /minhas_apostas history"
```

---

## Self-Review

**1. Spec coverage:**
- Default view (open/live full + settled summary line + button) → Tasks 3, 4, 5. ✓
- History view (paginated, recent-first, one button/game) → Tasks 2, 3, 4, 5. ✓
- Per-game detail (caller's own bets, ✓/✗ + points, total) → Tasks 3, 5. ✓
- Opcodes `mh`/`mg`/`mm`, dispatched by catch-all `on_callback` → Tasks 1, 5. ✓
- Repo aggregates (no N+1, no 500-row load) → Task 2. ✓
- Page clamping on stale buttons → Task 5 (`test_minhas_apostas_history_clamps_stale_page`). ✓
- Caller-scoping / no leak → Task 5 (`test_my_game_detail_scoped_to_caller`). ✓
- Empty/edge states → Task 5 (existing pending test kept) + `_render_my_bets_default` early return. ✓
- Maintenance rule (`/ajuda` + `COMPLETION.md`) → Task 6. ✓
- Testing across codec/repo/text/keyboard/handler → Tasks 1-5. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows full test bodies. ✓

**3. Type consistency:** `SettledSummary`/`SettledGameRow` fields are referenced identically in Tasks 2 and 5 (`game_count`, `bet_count`, `correct_count`, `points`, `home_goals_90`, `away_goals_90`). `my_history_game_label` takes `wrong` (computed as `bet_count - correct_count` at the call site). `my_game_detail_text` `lines` is `(description, is_correct, points)` in both the renderer (Task 3) and the handler (Task 5). Opcode names (`MyHistory`/`MyGameDetail`/`MyBetsHome`) match across Tasks 1, 4, 5. ✓

## Notes / gotchas

- `on_callback` is the **unpatterned catch-all** `CallbackQueryHandler` registered last; the board/palpite handlers use `^bv:`/`^gb:`/`^pjt:`/`^pjc:`/`^pv:` patterns and don't match `mh`/`mg`/`mm`, so the new opcodes fall through to `on_callback` with **no new registration** needed.
- The `match` in `on_callback` is intentionally non-exhaustive (no `case _`) — this is the existing style and is fine under `mypy --strict` because the result isn't used as an expression.
- `describe_bet` output is kept **unescaped** in `my_game_detail_text`, matching the existing `_existing_bets_text` convention (team names from API-Football don't contain HTML metachars).
- Scores use ASCII `x` to match `_game_label` / `game_board_text`.
- `order_by(func.max(Game.settled_at).desc())` avoids any GROUP BY ambiguity (all bets in a group share one game's `settled_at`).
