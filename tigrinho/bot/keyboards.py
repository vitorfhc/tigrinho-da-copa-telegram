"""Inline keyboard builders (COMPLETION.md §8.2).

All wizard buttons carry compact ``callback_data`` (see :mod:`tigrinho.bot.callbacks`). The exact
score pad offers 0–9 plus a ``10+`` catch-all per side (covers every realistic 90′ World Cup
score). The winner keyboard **hides DRAW for knockout fixtures** (§8.1).
"""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tigrinho.bot.callbacks import (
    BoardView,
    BttsInput,
    CallbackData,
    Cancel,
    ChooseCategory,
    ChooseGame,
    DeleteBet,
    ExactScore,
    FirstTeamInput,
    GameBoard,
    GamesBoardCompute,
    GamesBoardToggle,
    HomeScore,
    MyBetsHome,
    MyGameDetail,
    MyHistory,
    OverUnderInput,
    PalpiteView,
    TournamentAction,
    TournamentAddToggle,
    WinnerInput,
    encode,
)
from tigrinho.domain.bets import BttsSel, FirstTeamSel, OverUnderSel, WinnerSel
from tigrinho.domain.text_pt import (
    CATEGORY_ORDER,
    OVER_UNDER_LABELS,
    btts_labels,
    category_button_label,
)
from tigrinho.enums import Stage, TournamentStatus

MAX_SCORE_PER_SIDE = 10


def _button(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=encode(data))


def deep_link_url(bot_username: str, fixture_id: int) -> str:
    """Deep link into the DM betting wizard for a fixture (§8.2)."""
    return f"https://t.me/{bot_username}?start=bet_{fixture_id}"


def announcement_keyboard(
    games: Sequence[tuple[int, str]], bot_username: str
) -> InlineKeyboardMarkup:
    """One 🎯 Apostar URL button per open game. Each item: (fixture_id, label)."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎯 Apostar: {label}",
                url=deep_link_url(bot_username, fixture_id),
            )
        ]
        for fixture_id, label in games
    ]
    return InlineKeyboardMarkup(rows)


def games_keyboard(games: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Open-games picker for the /apostar wizard. Each item: (fixture_id, label)."""
    rows = [[_button(label, ChooseGame(fixture_id))] for fixture_id, label in games]
    return InlineKeyboardMarkup(rows)


def palpite_games_keyboard(games: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Next-24h games picker for /palpite — tapping one shows that game's AI palpite (§20).

    Each item: (fixture_id, label).
    """
    rows = [[_button(label, PalpiteView(fixture_id))] for fixture_id, label in games]
    return InlineKeyboardMarkup(rows)


def category_keyboard(fixture_id: int) -> InlineKeyboardMarkup:
    """The five bet categories for a fixture."""
    rows = [
        [_button(category_button_label(category), ChooseCategory(fixture_id, category))]
        for category in CATEGORY_ORDER
    ]
    rows.append([_button("✖️ Cancelar", Cancel())])
    return InlineKeyboardMarkup(rows)


def home_score_keyboard(fixture_id: int) -> InlineKeyboardMarkup:
    """Number pad (0–9 plus 10+) for the home side; selecting opens the away pad."""
    rows = [
        [_button(str(n), HomeScore(fixture_id, n)) for n in range(0, 5)],
        [_button(str(n), HomeScore(fixture_id, n)) for n in range(5, 10)],
        [_button("10+", HomeScore(fixture_id, MAX_SCORE_PER_SIDE))],
    ]
    return InlineKeyboardMarkup(rows)


def away_score_keyboard(fixture_id: int, home: int) -> InlineKeyboardMarkup:
    """Away number pad; each button finalizes the exact score (home baked into callback_data)."""
    rows = [
        [_button(str(n), ExactScore(fixture_id, home, n)) for n in range(0, 5)],
        [_button(str(n), ExactScore(fixture_id, home, n)) for n in range(5, 10)],
        [_button("10+", ExactScore(fixture_id, home, MAX_SCORE_PER_SIDE))],
    ]
    return InlineKeyboardMarkup(rows)


def winner_keyboard(
    fixture_id: int, stage: Stage, home_team: str, away_team: str
) -> InlineKeyboardMarkup:
    """HOME / DRAW / AWAY — DRAW hidden for knockout fixtures (§8.1)."""
    rows = [[_button(home_team, WinnerInput(fixture_id, WinnerSel.HOME))]]
    if stage is not Stage.KNOCKOUT:
        rows.append([_button("Empate", WinnerInput(fixture_id, WinnerSel.DRAW))])
    rows.append([_button(away_team, WinnerInput(fixture_id, WinnerSel.AWAY))])
    return InlineKeyboardMarkup(rows)


def btts_keyboard(fixture_id: int, home_team: str, away_team: str) -> InlineKeyboardMarkup:
    """Both-teams-to-score selector — the two "só o ..." options name the real teams."""
    labels = btts_labels(home_team, away_team)
    rows = [
        [_button(labels[sel], BttsInput(fixture_id, sel))]
        for sel in (BttsSel.BOTH, BttsSel.ONLY_HOME, BttsSel.ONLY_AWAY, BttsSel.NEITHER)
    ]
    return InlineKeyboardMarkup(rows)


def over_under_keyboard(fixture_id: int) -> InlineKeyboardMarkup:
    """Over/Under 2.5 selector."""
    rows = [
        [_button(OVER_UNDER_LABELS[sel], OverUnderInput(fixture_id, sel))]
        for sel in (OverUnderSel.OVER, OverUnderSel.UNDER)
    ]
    return InlineKeyboardMarkup(rows)


def first_team_keyboard(fixture_id: int, home_team: str, away_team: str) -> InlineKeyboardMarkup:
    """Which team scores first — the two real team names (no squads needed)."""
    return InlineKeyboardMarkup(
        [
            [_button(home_team, FirstTeamInput(fixture_id, FirstTeamSel.HOME))],
            [_button(away_team, FirstTeamInput(fixture_id, FirstTeamSel.AWAY))],
        ]
    )


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


def ended_games_keyboard(games: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Picker of recently-ended games for /placar_jogo. Each item: (fixture_id, label)."""
    rows = [[_button(label, GameBoard(fixture_id))] for fixture_id, label in games]
    return InlineKeyboardMarkup(rows)


def combined_games_keyboard(labels: Sequence[str], mask: int) -> InlineKeyboardMarkup:
    """Multi-select picker for /placar_jogos.

    ``labels`` in position order; ``mask`` = selected bits.
    """
    rows = [
        [_button(f"{'✅' if mask & (1 << i) else '☐'} {label}", GamesBoardToggle(mask, i))]
        for i, label in enumerate(labels)
    ]
    rows.append([_button(f"✅ Calcular placar ({mask.bit_count()})", GamesBoardCompute(mask))])
    return InlineKeyboardMarkup(rows)


def board_toggle_keyboard(weekly: bool) -> InlineKeyboardMarkup:
    """Toggle button switching the scoreboard between Geral and Semana (§10)."""
    if weekly:
        button = _button("📊 Ver Geral", BoardView("geral"))
    else:
        button = _button("📅 Ver Semana", BoardView("semana"))
    return InlineKeyboardMarkup([[button]])


# --- Bolãozinhos (Feature 7 / §22) ------------------------------------------------------------


def tournament_card_keyboard(
    tournament_id: int, status: TournamentStatus
) -> InlineKeyboardMarkup | None:
    """Creator management card buttons (None for terminal status). §5."""
    rows: list[list[InlineKeyboardButton]] = []
    if status is TournamentStatus.DRAFT:
        rows.append([_button("➕ Adicionar jogos", TournamentAction("ba", tournament_id))])
        rows.append([_button("📣 Abrir", TournamentAction("bo", tournament_id))])
    if status in (TournamentStatus.DRAFT, TournamentStatus.OPEN):
        rows.append([_button("❌ Cancelar", TournamentAction("bx", tournament_id))])
    return InlineKeyboardMarkup(rows) if rows else None


def tournament_add_picker_keyboard(
    tournament_id: int, games: Sequence[tuple[int, str, bool]]
) -> InlineKeyboardMarkup:
    """Identity-based multi-select of upcoming games (F18). Each item: (fixture_id, label, in)."""
    rows = [
        [
            _button(
                f"{'✅' if selected else '☐'} {label}",
                TournamentAddToggle(tournament_id, fixture_id),
            )
        ]
        for fixture_id, label, selected in games
    ]
    rows.append([_button("✔️ Pronto", TournamentAction("bd", tournament_id))])
    return InlineKeyboardMarkup(rows)


def tournament_list_keyboard(items: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """One details button per bolãozinho. Each item: (tournament_id, label)."""
    rows = [
        [_button(label, TournamentAction("bi", tournament_id))] for tournament_id, label in items
    ]
    return InlineKeyboardMarkup(rows)


def tournament_join_list_keyboard(items: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """One join-pick button per joinable bolãozinho. Each item: (tournament_id, label)."""
    rows = [
        [_button(label, TournamentAction("bj", tournament_id))] for tournament_id, label in items
    ]
    return InlineKeyboardMarkup(rows)


def tournament_join_card_keyboard(tournament_id: int, entry_label: str) -> InlineKeyboardMarkup:
    """Confirm-entry button shown on the /entrar card (label carries the price). §5."""
    return InlineKeyboardMarkup(
        [[_button(f"✅ Entrar ({entry_label})", TournamentAction("bk", tournament_id))]]
    )
