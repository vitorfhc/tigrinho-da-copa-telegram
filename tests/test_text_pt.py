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
    closed_bets_text,
    correction_text,
    describe_bet,
    describe_bet_value,
    format_kickoff_local,
    format_kickoff_short,
    format_money_cents,
    game_board_text,
    games_board_text,
    goal_cancelled_text,
    goal_text,
    help_text,
    kickoff_text,
    mention,
    my_game_detail_text,
    my_history_game_label,
    my_history_header,
    palpite_generating_text,
    palpite_no_games_text,
    palpite_no_key_text,
    palpite_text,
    points_table_text,
    reannounce_text,
    reminder_text,
    results_text,
    settled_summary_line,
    splitwise_admin_ready_text,
    splitwise_all_linked_text,
    splitwise_ask_email_text,
    splitwise_expense_description,
    splitwise_invalid_email_text,
    splitwise_link_intro_text,
    splitwise_link_required_text,
    splitwise_linked_text,
    splitwise_not_configured_text,
    tournament_no_result_text,
    tournament_result_text,
    tournament_standings_text,
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


def test_describe_bet_value_is_just_the_selection() -> None:
    # The value-only renderer drops the category prefix that describe_bet adds.
    assert describe_bet_value(ExactScorePayload(home=2, away=1)) == "2x1"
    assert (
        describe_bet_value(
            WinnerPayload(sel=WinnerSel.HOME), home_team="Brasil", away_team="Argentina"
        )
        == "Brasil"
    )
    assert describe_bet_value(WinnerPayload(sel=WinnerSel.DRAW)) == "Empate"
    assert describe_bet_value(BttsPayload(sel=BttsSel.BOTH)) == "Ambas marcam"
    assert (
        describe_bet_value(
            BttsPayload(sel=BttsSel.ONLY_AWAY), home_team="Brasil", away_team="Argentina"
        )
        == "Só o Argentina"
    )
    assert (
        describe_bet_value(OverUnderPayload(sel=OverUnderSel.UNDER)) == "Menos de 2.5 (até 2 gols)"
    )
    assert (
        describe_bet_value(
            FirstTeamPayload(sel=FirstTeamSel.AWAY), home_team="Brasil", away_team="Argentina"
        )
        == "Argentina"
    )


def test_describe_bet_value_escapes_team_names() -> None:
    assert (
        describe_bet_value(WinnerPayload(sel=WinnerSel.HOME), home_team="A & B", away_team="C")
        == "A &amp; B"
    )


def test_closed_bets_text_groups_by_category_in_order() -> None:
    items = [
        (BetCategory.WINNER, "João", "Brasil"),
        (BetCategory.EXACT_SCORE, "Felipe", "2x1"),
        (BetCategory.WINNER, "Felipe", "Brasil"),
        (BetCategory.EXACT_SCORE, "João", "1x0"),
    ]
    text = closed_bets_text(home="Brasil", away="Argentina", items=items)
    assert text is not None
    assert "Apostas fechadas" in text
    assert "Brasil x Argentina" in text
    # Categories follow CATEGORY_ORDER: Placar exato before Vencedor.
    assert text.index("Placar exato") < text.index("Vencedor")
    # Players sorted by name within a category: Felipe before João.
    exact_block = text[text.index("Placar exato") : text.index("Vencedor")]
    assert exact_block.index("Felipe") < exact_block.index("João")
    assert "• Felipe: 2x1" in text
    assert "• João: 1x0" in text


def test_closed_bets_text_omits_empty_categories() -> None:
    items = [(BetCategory.BTTS, "Ana", "Ambas marcam")]
    text = closed_bets_text(home="Brasil", away="Argentina", items=items)
    assert text is not None
    assert "Placar exato" not in text
    assert "Vencedor" not in text
    assert "• Ana: Ambas marcam" in text


def test_closed_bets_text_returns_none_when_no_bets() -> None:
    assert closed_bets_text(home="Brasil", away="Argentina", items=[]) is None


def test_closed_bets_text_escapes_player_and_team_names() -> None:
    items = [(BetCategory.WINNER, "Tig<rão> & cia", "Brasil")]
    text = closed_bets_text(home="A & B", away="C", items=items)
    assert text is not None
    assert "&lt;rão&gt;" in text
    assert "Tig&lt;rão&gt; &amp; cia" in text
    assert "A &amp; B x C" in text


def test_points_table_reflects_scoring() -> None:
    # The points table shows only the offered (new) two-market set.
    text = points_table_text()
    # EXACT_SCORE uses partial credit: +2 per team score, +1 outcome, up to 5 pts total
    assert "Placar exato" in text
    assert "<b>+2</b>" in text
    assert "até <b>5</b> pts" in text
    assert "Quem está na frente no 1º tempo: <b>2</b> pts" in text
    assert "Mais/Menos 2.5 gols" not in text  # removed-from-offer category absent


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
        "/placar_jogos",
        "/ajuda",
        "/start",
    )
    for command in commands:
        assert command in text
    assert "90 minutos" in text  # 90' rule
    assert "intervalo" in text.lower()  # half-time-result rule
    assert "fecham no apito" in text  # close-at-kickoff
    assert "privado" in text  # DM betting
    assert "Placar exato" in text  # categories present
    assert "Quem está na frente no 1º tempo" in text  # new half-time-result category
    assert "/palpite" in text  # AI palpite command
    assert "/entrar" in text  # bolãozinho join command
    assert "/bolaozinho_criar" in text  # bolãozinho create command
    assert "/bolaozinho_placar" in text  # bolãozinho partial-placar command
    assert "Bolãozinhos" in text  # bolãozinho section
    assert "Prêmio = pote − uma entrada" in text  # money rule
    assert "/vincular_splitwise" in text  # splitwise linking command (§23)
    assert "Splitwise" in text  # splitwise section


def test_help_mentions_daily_bolao() -> None:
    text = help_text()
    assert "todo dia" in text.lower() or "bolãozinho do dia" in text.lower()


def test_welcome_text_points_to_help() -> None:
    assert "/ajuda" in welcome_text()


def test_splitwise_text_functions() -> None:
    assert "já está no grupo do Splitwise" in splitwise_link_intro_text()
    assert "e-mail" in splitwise_ask_email_text()
    assert "Maria &amp; Cia" in splitwise_linked_text(member_name="Maria & Cia")  # HTML-escaped
    assert "válido" in splitwise_invalid_email_text()
    assert "vincule" in splitwise_link_required_text().lower()
    assert "vinculado" in splitwise_all_linked_text()
    assert "não está configurado" in splitwise_not_configured_text()
    # Expense description is plain text for the Splitwise API (not Telegram HTML) — no escaping.
    desc = splitwise_expense_description(name="Fase & Final", winners=["João", "Ana"])
    assert desc == "🏆 Bolãozinho 'Fase & Final' — João, Ana"
    assert splitwise_expense_description(name="X", winners=[]) == "🏆 Bolãozinho 'X' — —"
    admin = splitwise_admin_ready_text(tournament_id=7, name="Fase")
    assert "#7" in admin
    assert "/bolaozinho_splitwise" in admin


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
        curiosity="As seleções decidiram a última Copa América.",
    )
    assert "Brasil" in text and "Argentina" in text
    assert "2x1" in text  # exact score
    assert "Brasil joga em casa" in text  # analysis included
    assert "Copa América" in text  # curiosity rendered
    # every category label appears
    for label in ("Placar exato", "Primeira equipe", "Ambas marcam", "Vencedor", "Mais"):
        assert label in text


def test_palpite_text_omits_empty_curiosity() -> None:
    text = palpite_text(
        home="Brasil",
        away="Argentina",
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        analysis="ok",
        payloads=[WinnerPayload(sel=WinnerSel.HOME)],
        curiosity="",
    )
    assert "Curiosidade" not in text  # no empty curiosity line


def test_palpite_text_escapes_team_names_and_curiosity() -> None:
    text = palpite_text(
        home="A<b>X",
        away="Y&Z",
        kickoff_local=datetime(2026, 6, 16, 16, 0),
        analysis="ok",
        payloads=[WinnerPayload(sel=WinnerSel.HOME)],
        curiosity="fato com <tag> & cia",
    )
    assert "A<b>X" not in text  # raw HTML must be escaped
    assert "&amp;Z" in text
    assert "<tag>" not in text  # curiosity escaped too
    assert "&amp; cia" in text


def test_palpite_no_key_text_mentions_gemini() -> None:
    text = palpite_no_key_text()
    assert "Gemini" in text or "GEMINI_API_KEY" in text


def test_palpite_generating_text() -> None:
    assert len(palpite_generating_text()) > 0


def test_palpite_no_games_text() -> None:
    text = palpite_no_games_text()
    assert "24h" in text
    assert "andamento" in text  # also mentions in-progress (live) games


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


def test_correction_text_score_changed_shows_previous_and_deltas() -> None:
    text = correction_text(
        home="France",
        away="Senegal",
        home_goals=3,
        away_goals=1,
        corrected_from=(3, 0),
        first_team_name="France",
        players=[(42, "Alice", 5, 7, [("Resultado", True, 2), ("Ambas marcam", True, 2)])],
    )
    assert "Placar corrigido" in text
    assert "recalculados" in text
    assert "France 3 x 1 Senegal" in text
    assert "(antes: 3 x 0)" in text
    assert "tg://user?id=42" in text
    assert "5 → 7" in text  # per-player delta


def test_correction_text_outcome_only_change_omits_previous_score() -> None:
    # Score unchanged (first-scorer reclassified): no "(antes: …)" suffix.
    text = correction_text(
        home="France",
        away="Senegal",
        home_goals=3,
        away_goals=1,
        corrected_from=None,
        first_team_name="Senegal",
        players=[(42, "Alice", 2, 0, [("Primeira equipe a marcar", False, 0)])],
    )
    assert "France 3 x 1 Senegal" in text
    assert "antes:" not in text
    assert "Primeira equipe a marcar: Senegal" in text
    assert "2 → 0" in text  # a player who LOST points


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
            ("Brasil", "Argentina", datetime(2026, 6, 13, 16, 0), [("Felipe", 1)], 2),
            ("França", "Alemanha", datetime(2026, 6, 13, 16, 0), [], 2),
        ]
    )
    assert "Falta ~1h" in text
    assert "Brasil x Argentina — Sáb 13/06 16:00" in text  # 2026-06-13 is a Saturday
    assert "França x Alemanha — Sáb 13/06 16:00" in text
    assert "🎯 Apostar" in text


def test_reminder_text_escapes_team_names() -> None:
    text = reminder_text([("A & B", "C > D", datetime(2026, 6, 13, 16, 0), [], 2)])
    assert "A &amp; B" in text
    assert "C &gt; D" in text


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
    text = games_board_text(games=[("A & B", "C", 2, 1)], rows=[(1, "Z & Y", 5)])
    assert "Placar — 1 jogo" in text
    assert "A &amp; B 2x1 C" in text
    assert "Z &amp; Y" in text  # player name escaped in the medal row


def test_games_board_text_no_bettors() -> None:
    text = games_board_text(games=[("A", "B", 1, 0)], rows=[])
    assert "Ninguém apostou nesses jogos" in text


def test_reminder_text_lists_bettors_with_counts() -> None:
    text = reminder_text(
        [("Brasil", "Argentina", datetime(2026, 6, 13, 16, 0), [("Felipe", 1), ("Ana", 2)], 2)]
    )
    # The denominator is the game's offered category count (2 for the new set).
    assert "👥 Já palpitaram: Felipe (1/2), Ana (2/2)" in text


def test_reminder_text_no_bettors_shows_nudge() -> None:
    text = reminder_text([("Brasil", "Argentina", datetime(2026, 6, 13, 16, 0), [], 2)])
    assert "Ninguém palpitou ainda" in text


def test_reminder_text_escapes_bettor_names() -> None:
    text = reminder_text([("Brasil", "Argentina", datetime(2026, 6, 13, 16, 0), [("A & B", 1)], 2)])
    assert "A &amp; B (1/2)" in text


def test_kickoff_text() -> None:
    text = kickoff_text("Brasil", "Argentina")
    assert "Bola rolando" in text
    assert "Brasil x Argentina" in text


def test_goal_text_names_team_and_running_score() -> None:
    # Derived from the live score feed only: scoring team + running score, no scorer/minute (§9.4).
    text = goal_text(
        scoring_team="Brasil",
        home_team="Brasil",
        away_team="Argentina",
        home_score=1,
        away_score=0,
    )
    assert "GOL do Brasil" in text
    assert "Brasil 1 x 0 Argentina" in text


def test_goal_text_escapes_html() -> None:
    text = goal_text(
        scoring_team="A&B",
        home_team="A&B",
        away_team="C<D",
        home_score=0,
        away_score=1,
    )
    assert "&amp;" in text
    assert "&lt;" in text


def test_goal_cancelled_text_names_team_and_running_score() -> None:
    # The live score split tells us which team's goal vanished, so the team is always named (§9.4).
    text = goal_cancelled_text(
        scoring_team="Argentina",
        home_team="Brasil",
        away_team="Argentina",
        home_score=0,
        away_score=0,
    )
    assert "anulado pelo VAR" in text
    assert "Argentina" in text
    assert "Brasil 0 x 0 Argentina" in text


def test_goal_cancelled_text_escapes_html() -> None:
    text = goal_cancelled_text(
        scoring_team="A&B",
        home_team="A&B",
        away_team="C<D",
        home_score=0,
        away_score=0,
    )
    assert "&amp;" in text
    assert "&lt;" in text


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
        home="Brasil",
        away="Croácia",
        home_goals=2,
        away_goals=1,
        correct=3,
        wrong=1,
        points=12,
    )
    assert label == "Brasil 2x1 Croácia · 3✓1✗ +12 pts"
    assert "<" not in label  # button labels are not HTML-parsed


def test_my_game_detail_text() -> None:
    text = my_game_detail_text(
        home="Brasil",
        away="Croácia",
        home_goals=2,
        away_goals=1,
        lines=[("Placar exato: 2x1", True, 5), ("Ambas marcam: Não", False, 0)],
    )
    assert "Brasil 2 x 1 Croácia" in text
    assert "• Placar exato: 2x1 — ✓ 5 pts" in text
    assert "• Ambas marcam: Não — ✗ 0 pts" in text
    assert "Total: +5 pts" in text


def test_format_money_cents_pt_br() -> None:
    assert format_money_cents(9000, currency="R$") == "R$ 90,00"
    assert format_money_cents(99, currency="R$") == "R$ 0,99"
    assert format_money_cents(1285, currency="US$") == "US$ 12,85"
    assert format_money_cents(0, currency="R$") == "R$ 0,00"
    assert format_money_cents(9000, currency="R$", decimals=0) == "R$ 9000"


def test_tournament_result_single_winner() -> None:
    text = tournament_result_text(
        name="Oitavas",
        n_entrants=10,
        pot_cents=10000,
        prize_cents=9000,
        winners=[(100, "Ana", 14)],
        per_winner_cents=9000,
        remainder_cents=0,
        is_correction=False,
        currency="R$",
    )
    assert "🏆" in text and "encerrado" in text
    assert "Pote: R$ 100,00 (10 entradas)" in text
    assert "Prêmio: R$ 90,00" in text
    assert "tg://user?id=100" in text
    assert "Leva R$ 90,00" in text


def test_tournament_result_tie_shows_split_and_remainder() -> None:
    text = tournament_result_text(
        name="X",
        n_entrants=3,
        pot_cents=3000,
        prize_cents=2000,
        winners=[(1, "A", 12), (2, "B", 12), (3, "C", 12)],
        per_winner_cents=666,
        remainder_cents=2,
        is_correction=True,
        currency="R$",
    )
    assert "corrigido" in text
    assert "Empate (3)" in text
    assert "Cada um leva R$ 6,66 (sobra R$ 0,02)" in text


def test_tournament_no_result_text() -> None:
    assert "sem resultado" in tournament_no_result_text(name="Y")


def test_tournament_standings_partial_with_hint() -> None:
    text = tournament_standings_text(
        name="Oitavas <b>",
        settled_count=2,
        total_games=4,
        n_entrants=3,
        pot_cents=10000,
        prize_cents=9000,
        standings=[("Ana", 12), ("Bruno", 9), ("Cau", 7), ("Dida", 4)],
        currency="R$",
        decimals=2,
    )
    assert "📊 Placar parcial" in text
    assert "2/4 jogos" in text
    assert "🥇 Ana" in text and "🥈 Bruno" in text and "🥉 Cau" in text
    assert "4. Dida" in text  # no medal past 3rd
    assert "&lt;b&gt;" in text  # name HTML-escaped (no raw tag)
    assert "/bolaozinho_placar" in text  # the on-demand hint by default


def test_tournament_standings_final_drops_hint() -> None:
    text = tournament_standings_text(
        name="Final",
        settled_count=3,
        total_games=3,
        n_entrants=2,
        pot_cents=2000,
        prize_cents=1000,
        standings=[("Ana", 5)],
        currency="R$",
        is_final=True,
        with_hint=False,
    )
    assert "🏁 Placar final" in text
    assert "/bolaozinho_placar" not in text


def test_tournament_standings_empty_placeholder() -> None:
    text = tournament_standings_text(
        name="Vazio",
        settled_count=1,
        total_games=2,
        n_entrants=1,
        pot_cents=1000,
        prize_cents=0,
        standings=[],
        currency="R$",
    )
    assert "Ninguém pontuou" in text
