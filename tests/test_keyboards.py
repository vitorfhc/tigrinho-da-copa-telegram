"""Tests for inline keyboard builders (COMPLETION.md §8.2)."""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from tigrinho.bot.callbacks import (
    BttsInput,
    CallbackData,
    ChooseCategory,
    ChooseGame,
    ExactScore,
    FirstTeamInput,
    GamesBoardCompute,
    GamesBoardToggle,
    HomeScore,
    MyBetsHome,
    MyGameDetail,
    MyHistory,
    PalpiteView,
    SplitwiseInGroup,
    SplitwiseMemberPick,
    SplitwiseNotInGroup,
    SplitwiseRegisterPick,
    WinnerInput,
    decode,
)
from tigrinho.bot.keyboards import (
    announcement_keyboard,
    away_score_keyboard,
    btts_keyboard,
    category_keyboard,
    combined_games_keyboard,
    deep_link_url,
    first_team_keyboard,
    games_keyboard,
    home_score_keyboard,
    my_bets_keyboard,
    my_game_detail_keyboard,
    my_history_keyboard,
    over_under_keyboard,
    palpite_games_keyboard,
    splitwise_intro_keyboard,
    splitwise_link_button,
    splitwise_member_keyboard,
    splitwise_register_keyboard,
    winner_keyboard,
)
from tigrinho.domain.bets import FirstTeamSel, WinnerSel
from tigrinho.enums import Stage


def _decoded(markup: InlineKeyboardMarkup) -> list[CallbackData]:
    return [
        decode(b.callback_data)
        for row in markup.inline_keyboard
        for b in row
        if isinstance(b.callback_data, str)
    ]


def test_deep_link_url() -> None:
    assert deep_link_url("TigrinhoDaCopaBot", 123) == "https://t.me/TigrinhoDaCopaBot?start=bet_123"


def test_announcement_keyboard_one_button_per_game() -> None:
    keyboard = announcement_keyboard(
        [(1, "Brasil x Argentina"), (2, "França x Alemanha")], "TigrinhoDaCopaBot"
    )
    assert len(keyboard.inline_keyboard) == 2
    button = keyboard.inline_keyboard[0][0]
    assert "Apostar" in button.text
    assert button.url == "https://t.me/TigrinhoDaCopaBot?start=bet_1"


def test_games_keyboard() -> None:
    assert _decoded(games_keyboard([(1001, "Brasil x Argentina")])) == [ChooseGame(1001)]


def test_splitwise_intro_keyboard() -> None:
    assert _decoded(splitwise_intro_keyboard()) == [SplitwiseInGroup(), SplitwiseNotInGroup()]


def test_splitwise_member_keyboard_has_picks_plus_fallback() -> None:
    decoded = _decoded(splitwise_member_keyboard([(11, "João"), (22, "Maria")]))
    assert decoded == [
        SplitwiseMemberPick(11),
        SplitwiseMemberPick(22),
        SplitwiseNotInGroup(),
    ]


def test_splitwise_register_keyboard() -> None:
    assert _decoded(splitwise_register_keyboard([(7, "Fase de Grupos")])) == [
        SplitwiseRegisterPick(7)
    ]


def test_splitwise_link_button_is_deep_link() -> None:
    keyboard = splitwise_link_button("TigrinhoDaCopaBot")
    button = keyboard.inline_keyboard[0][0]
    assert button.url == "https://t.me/TigrinhoDaCopaBot?start=vincular"


def test_palpite_games_keyboard_one_button_per_game() -> None:
    keyboard = palpite_games_keyboard(
        [(1001, "Brasil x Argentina · 16/06 16:00"), (1002, "França x Alemanha · 16/06 19:00")]
    )
    assert _decoded(keyboard) == [PalpiteView(1001), PalpiteView(1002)]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["Brasil x Argentina · 16/06 16:00", "França x Alemanha · 16/06 19:00"]


def test_category_keyboard_has_five_categories() -> None:
    categories = [d for d in _decoded(category_keyboard(1001)) if isinstance(d, ChooseCategory)]
    assert len(categories) == 5
    assert all(c.fixture_id == 1001 for c in categories)


def test_category_keyboard_buttons_show_points() -> None:
    labels = [button.text for row in category_keyboard(1001).inline_keyboard for button in row]
    assert "Placar exato · 5 pts" in labels
    assert "Primeira equipe a marcar · 2 pts" in labels
    assert "Mais/Menos 2.5 gols · 1 pt" in labels  # singular for 1


def test_home_score_pad_has_zero_to_ten() -> None:
    values = sorted(
        d.value for d in _decoded(home_score_keyboard(1001)) if isinstance(d, HomeScore)
    )
    assert values == list(range(0, 11))


def test_away_score_pad_bakes_in_home() -> None:
    decoded = [d for d in _decoded(away_score_keyboard(1001, 2)) if isinstance(d, ExactScore)]
    assert sorted(d.away for d in decoded) == list(range(0, 11))
    assert all(d.home == 2 for d in decoded)


def test_winner_keyboard_hides_draw_for_knockout() -> None:
    group_sels = {
        d.sel
        for d in _decoded(winner_keyboard(1001, Stage.GROUP, "Brasil", "Argentina"))
        if isinstance(d, WinnerInput)
    }
    knockout_sels = {
        d.sel
        for d in _decoded(winner_keyboard(1001, Stage.KNOCKOUT, "Brasil", "Argentina"))
        if isinstance(d, WinnerInput)
    }
    assert WinnerSel.DRAW in group_sels
    assert knockout_sels == {WinnerSel.HOME, WinnerSel.AWAY}


def test_btts_and_over_under() -> None:
    keyboard = btts_keyboard(1001, "Brasil", "Argentina")
    btts = _decoded(keyboard)
    assert len(btts) == 4
    assert all(isinstance(d, BttsInput) for d in btts)
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["Ambas marcam", "Só o Brasil", "Só o Argentina", "Nenhuma marca"]
    assert len(_decoded(over_under_keyboard(1001))) == 2


def test_first_team_keyboard() -> None:
    decoded = _decoded(first_team_keyboard(1001, "Brasil", "Argentina"))
    sels = {d.sel for d in decoded if isinstance(d, FirstTeamInput)}
    assert sels == {FirstTeamSel.HOME, FirstTeamSel.AWAY}
    keyboard = first_team_keyboard(1001, "Brasil", "Argentina")
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["Brasil", "Argentina"]


def test_combined_games_keyboard_toggles_and_compute() -> None:
    keyboard = combined_games_keyboard(["A x B", "C x D"], mask=0b10)
    row0 = keyboard.inline_keyboard[0][0]
    assert row0.text == "☐ A x B"
    assert isinstance(row0.callback_data, str)
    assert decode(row0.callback_data) == GamesBoardToggle(0b10, 0)
    row1 = keyboard.inline_keyboard[1][0]
    assert row1.text == "✅ C x D"
    assert isinstance(row1.callback_data, str)
    assert decode(row1.callback_data) == GamesBoardToggle(0b10, 1)
    compute = keyboard.inline_keyboard[2][0]
    assert compute.text == "✅ Calcular placar (1)"
    assert isinstance(compute.callback_data, str)
    assert decode(compute.callback_data) == GamesBoardCompute(0b10)


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
