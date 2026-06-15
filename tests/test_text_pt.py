"""Tests for pt-BR templates (COMPLETION.md §11)."""

from __future__ import annotations

from datetime import datetime

from tigrinho.domain.bets import (
    BttsPayload,
    BttsSel,
    ExactScorePayload,
    FirstScorerPayload,
    OverUnderPayload,
    OverUnderSel,
    WinnerPayload,
    WinnerSel,
)
from tigrinho.domain.text_pt import (
    announcement_text,
    describe_bet,
    format_kickoff_local,
    help_text,
    mention,
    points_table_text,
    reannounce_text,
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
    assert "Mais de 2.5" in describe_bet(OverUnderPayload(sel=OverUnderSel.OVER))
    assert describe_bet(FirstScorerPayload(player_id=7)) == "Primeiro a marcar: jogador #7"
    assert (
        describe_bet(FirstScorerPayload(player_id=7), scorer_name="Neymar")
        == "Primeiro a marcar: Neymar"
    )


def test_points_table_reflects_scoring() -> None:
    text = points_table_text()
    assert "Placar exato: <b>5</b> pts" in text
    assert "Primeiro a marcar: <b>4</b> pts" in text
    assert "Mais/Menos 2.5 gols: <b>1</b> pts" in text


def test_help_text_covers_required_content() -> None:
    text = help_text()
    for command in ("/apostar", "/minhas_apostas", "/jogos", "/placar", "/ajuda", "/start"):
        assert command in text
    assert "90 minutos" in text  # 90' rule
    assert "mata-mata" in text.lower()  # knockout rule
    assert "fecham no apito" in text  # close-at-kickoff
    assert "privado" in text  # DM betting
    assert "Placar exato" in text  # categories present


def test_welcome_text_points_to_help() -> None:
    assert "/ajuda" in welcome_text()


def test_format_kickoff_local() -> None:
    kickoff = datetime(2026, 6, 16, 16, 0)
    expected_weekday = _WEEKDAYS_PT[kickoff.weekday()]
    assert format_kickoff_local(kickoff) == f"{expected_weekday} 16/06 16:00"


def test_announcement_text() -> None:
    text = announcement_text([("Brasil", "Argentina", datetime(2026, 6, 16, 16, 0))])
    assert "Novos jogos" in text
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
        scorer_name="Neymar",
        players=[(42, "Alice", 7, [("Placar exato", True, 5), ("Vencedor", True, 2)])],
    )
    assert "Brasil 2 x 1 Argentina" in text
    assert "Primeiro a marcar: Neymar" in text
    assert "tg://user?id=42" in text
    assert "✓ Placar exato (+5)" in text


def test_results_text_no_scorer_and_no_players() -> None:
    text = results_text(
        home="Brasil", away="Argentina", home_goals=0, away_goals=0, scorer_name=None, players=[]
    )
    assert "Sem gol válido" in text
    assert "Ninguém apostou" in text
