"""Tests for pt-BR templates (COMPLETION.md §11)."""

from __future__ import annotations

from datetime import datetime

from tigrinho.domain.bets import (
    BetCategory,
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstTeamPayload,
    FirstTeamSel,
    OverUnderPayload,
    OverUnderSel,
    Payload,
    WinnerPayload,
    WinnerSel,
)
from tigrinho.domain.text_pt import (
    announcement_text,
    board_text,
    category_button_label,
    describe_bet,
    format_kickoff_local,
    format_kickoff_short,
    game_board_text,
    help_text,
    mention,
    palpite_no_games_text,
    palpite_no_key_text,
    palpite_text,
    points_table_text,
    reannounce_text,
    reminder_text,
    results_text,
    void_text,
    welcome_text,
)

_WEEKDAYS_PT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def test_mention_escapes_name() -> None:
    out = mention(123, "Tig<rão> & cia")
    assert out.startswith('<a href="tg://user?id=123">')
    assert "&lt;rão&gt;" in out
    assert "&amp;" in out


def test_describe_bet_all_categories() -> None:
    assert describe_bet(ExactScorePayload(home=2, away=1)) == "Placar exato: 2x1"
    assert (
        describe_bet(WinnerPayload(sel=WinnerSel.HOME), home_team="Brasil", away_team="Argentina")
        == "Vencedor: Brasil"
    )
    assert describe_bet(WinnerPayload(sel=WinnerSel.DRAW)) == "Vencedor: Empate"
    assert describe_bet(BttsPayload(sel=BttsSel.NEITHER)) == "Ambas marcam: Nenhuma marca"
    assert (
        describe_bet(BttsPayload(sel=BttsSel.ONLY_HOME), home_team="Brasil", away_team="Argentina")
        == "Ambas marcam: Só o Brasil"
    )
    assert "Mais de 2.5" in describe_bet(OverUnderPayload(sel=OverUnderSel.OVER))
    assert (
        describe_bet(
            FirstTeamPayload(sel=FirstTeamSel.HOME), home_team="Brasil", away_team="Argentina"
        )
        == "Primeira equipe a marcar: Brasil"
    )
    assert (
        describe_bet(
            FirstTeamPayload(sel=FirstTeamSel.AWAY), home_team="Brasil", away_team="Argentina"
        )
        == "Primeira equipe a marcar: Argentina"
    )


def test_points_table_reflects_scoring() -> None:
    text = points_table_text()
    assert "Placar exato: <b>5</b> pts" in text
    assert "Primeira equipe a marcar: <b>2</b> pts" in text
    assert "Mais/Menos 2.5 gols: <b>1</b> pts" in text


def test_category_button_label_includes_points() -> None:
    assert category_button_label(BetCategory.EXACT_SCORE) == "Placar exato · 5 pts"
    assert category_button_label(BetCategory.FIRST_TEAM) == "Primeira equipe a marcar · 2 pts"
    # 1 point uses the singular unit
    assert category_button_label(BetCategory.OVER_UNDER) == "Mais/Menos 2.5 gols · 1 pt"


def test_help_text_covers_required_content() -> None:
    text = help_text()
    commands = (
        "/apostar",
        "/minhas_apostas",
        "/jogos",
        "/placar",
        "/placar_jogo",
        "/ajuda",
        "/start",
    )
    for command in commands:
        assert command in text
    assert "90 minutos" in text  # 90' rule
    assert "mata-mata" in text.lower()  # knockout rule
    assert "fecham no apito" in text  # close-at-kickoff
    assert "privado" in text  # DM betting
    assert "Placar exato" in text  # categories present
    assert "Primeira equipe a marcar" in text  # team-based first-scorer category
    assert "/palpite" in text  # AI palpite command


def test_welcome_text_points_to_help() -> None:
    assert "/ajuda" in welcome_text()


def test_palpite_text_renders_each_category() -> None:
    payloads: list[Payload] = [
        ExactScorePayload(home=2, away=1),
        FirstTeamPayload(sel=FirstTeamSel.HOME),
        BttsPayload(sel=BttsSel.BOTH),
        WinnerPayload(sel=WinnerSel.HOME),
        OverUnderPayload(sel=OverUnderSel.OVER),
    ]
    text = palpite_text(
        home="Brasil",
        away="Argentina",
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        analysis="Brasil joga em casa e está em alta.",
        payloads=payloads,
        confidence=70,
    )
    assert "Brasil" in text and "Argentina" in text
    assert "2x1" in text  # exact score
    assert "Brasil joga em casa" in text  # analysis included
    assert "70%" in text  # confidence rendered
    # every category label appears
    for label in ("Placar exato", "Primeira equipe", "Ambas marcam", "Vencedor", "Mais"):
        assert label in text


def test_palpite_text_escapes_team_names() -> None:
    text = palpite_text(
        home="A<b>X",
        away="Y&Z",
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        analysis="ok",
        payloads=[WinnerPayload(sel=WinnerSel.HOME)],
        confidence=None,
    )
    assert "A<b>X" not in text  # raw HTML must be escaped
    assert "&amp;Z" in text


def test_palpite_no_key_text_mentions_gemini() -> None:
    text = palpite_no_key_text()
    assert "Gemini" in text or "GEMINI_API_KEY" in text


def test_palpite_no_games_text() -> None:
    assert "24h" in palpite_no_games_text()


def test_format_kickoff_local() -> None:
    kickoff = datetime(2026, 6, 16, 16, 0)
    expected_weekday = _WEEKDAYS_PT[kickoff.weekday()]
    assert format_kickoff_local(kickoff) == f"{expected_weekday} 16/06 16:00"


def test_format_kickoff_short() -> None:
    assert format_kickoff_short(datetime(2026, 6, 16, 16, 0)) == "16/06 16:00"


def test_announcement_text() -> None:
    text = announcement_text([("Brasil", "Argentina", datetime(2026, 6, 16, 16, 0))])
    assert "próximas 24h" in text
    assert "Brasil x Argentina" in text
    assert "16/06 16:00" in text


def test_reannounce_text() -> None:
    text = reannounce_text("Brasil", "Argentina", datetime(2026, 6, 16, 16, 0))
    assert "remarcado" in text
    assert "Brasil x Argentina" in text


def test_void_text() -> None:
    text = void_text("Brasil", "Argentina")
    assert "anuladas" in text
    assert "Brasil x Argentina" in text


def test_results_text_with_players() -> None:
    text = results_text(
        home="Brasil",
        away="Argentina",
        home_goals=2,
        away_goals=1,
        first_team_name="Brasil",
        players=[(42, "Alice", 7, [("Placar exato", True, 5), ("Vencedor", True, 2)])],
    )
    assert "Brasil 2 x 1 Argentina" in text
    assert "Primeira equipe a marcar: Brasil" in text
    assert "tg://user?id=42" in text
    assert "✓ Placar exato (+5)" in text


def test_results_text_no_team_and_no_players() -> None:
    text = results_text(
        home="Brasil",
        away="Argentina",
        home_goals=0,
        away_goals=0,
        first_team_name=None,
        players=[],
    )
    assert "Sem gol válido" in text
    assert "Ninguém apostou" in text


def test_board_text_geral_with_medals() -> None:
    text = board_text(weekly=False, rows=[(1, "Alice", 10), (2, "Bob", 5), (3, "Cau", 1)])
    assert "Placar Geral" in text
    assert "🥇 Alice" in text
    assert "🥈 Bob" in text
    assert "🥉 Cau" in text


def test_board_text_weekly_and_caller_outside_top() -> None:
    text = board_text(weekly=True, rows=[(1, "A", 10)], caller_outside=(20, 3))
    assert "Placar da Semana" in text
    assert "Você: 20º" in text


def test_board_text_empty() -> None:
    assert "Ainda não há pontos" in board_text(weekly=False, rows=[])


def test_game_board_text_with_score_and_medals() -> None:
    text = game_board_text(
        home="Brasil",
        away="Argentina",
        home_goals=2,
        away_goals=1,
        rows=[(1, "Alice", 8), (2, "Bob", 3)],
    )
    assert "Placar do jogo" in text
    assert "Brasil 2 x 1 Argentina" in text
    assert "🥇 Alice — <b>8</b> pts" in text
    assert "🥈 Bob" in text


def test_game_board_text_escapes_team_names() -> None:
    text = game_board_text(
        home="A & B", away="C <i>D</i>", home_goals=0, away_goals=0, rows=[(1, "Z", 0)]
    )
    assert "A &amp; B" in text
    assert "C &lt;i&gt;D&lt;/i&gt;" in text


def test_game_board_text_no_bettors() -> None:
    text = game_board_text(home="A", away="B", home_goals=1, away_goals=0, rows=[])
    assert "Ninguém apostou" in text


def test_reminder_text_lists_games_with_weekday() -> None:
    text = reminder_text(
        [
            ("Brasil", "Argentina", datetime(2026, 6, 13, 16, 0)),
            ("França", "Alemanha", datetime(2026, 6, 13, 16, 0)),
        ]
    )
    assert "Falta ~1h" in text
    assert "Brasil x Argentina — Sáb 13/06 16:00" in text  # 2026-06-13 is a Saturday
    assert "França x Alemanha — Sáb 13/06 16:00" in text
    assert "🎯 Apostar" in text


def test_reminder_text_escapes_team_names() -> None:
    text = reminder_text([("A & B", "C > D", datetime(2026, 6, 13, 16, 0))])
    assert "A &amp; B" in text
    assert "C &gt; D" in text
