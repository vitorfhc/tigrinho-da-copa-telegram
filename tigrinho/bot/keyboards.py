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
    HomeScore,
    OverUnderInput,
    WinnerInput,
    encode,
)
from tigrinho.domain.bets import BttsSel, FirstTeamSel, OverUnderSel, WinnerSel
from tigrinho.domain.text_pt import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    OVER_UNDER_LABELS,
    btts_labels,
)
from tigrinho.enums import Stage

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


def category_keyboard(fixture_id: int) -> InlineKeyboardMarkup:
    """The five bet categories for a fixture."""
    rows = [
        [_button(CATEGORY_LABELS[category], ChooseCategory(fixture_id, category))]
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


def my_bets_keyboard(open_bets: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """A 🗑 Apagar button per still-open bet. Each item: (bet_id, label)."""
    rows = [[_button(f"🗑 Apagar: {label}", DeleteBet(bet_id))] for bet_id, label in open_bets]
    return InlineKeyboardMarkup(rows)


def board_toggle_keyboard(weekly: bool) -> InlineKeyboardMarkup:
    """Toggle button switching the scoreboard between Geral and Semana (§10)."""
    if weekly:
        button = _button("📊 Ver Geral", BoardView("geral"))
    else:
        button = _button("📅 Ver Semana", BoardView("semana"))
    return InlineKeyboardMarkup([[button]])
