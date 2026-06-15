"""Tests for pt-BR templates (COMPLETION.md §11)."""

from __future__ import annotations

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
    describe_bet,
    help_text,
    mention,
    points_table_text,
    welcome_text,
)


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
