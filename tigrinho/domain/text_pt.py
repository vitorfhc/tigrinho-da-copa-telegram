"""pt-BR message templates, HTML parse mode (COMPLETION.md §3, §11).

PURE string builders (no I/O). Telegram uses ``ParseMode.HTML`` everywhere, so all
user-supplied text is escaped via :func:`escape`, and player mentions use HTML inline mentions
``<a href="tg://user?id=…">name</a>`` (work without an @username).

**Maintenance rule (§11):** any change to commands, categories, scoring or grading rules MUST
update :func:`help_text` here **and** ``COMPLETION.md`` in the same change.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime
from typing import assert_never

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
from tigrinho.domain.scoring import POINTS
from tigrinho.enums import TournamentStatus

CATEGORY_LABELS: dict[BetCategory, str] = {
    BetCategory.EXACT_SCORE: "Placar exato",
    BetCategory.FIRST_TEAM: "Primeira equipe a marcar",
    BetCategory.BTTS: "Ambas marcam",
    BetCategory.WINNER: "Vencedor",
    BetCategory.OVER_UNDER: "Mais/Menos 2.5 gols",
}

# Total number of bet categories — the "5" in "3/5" (one bet per category per game).
TOTAL_CATEGORIES = len(BetCategory)


def btts_labels(home_team: str, away_team: str) -> dict[BttsSel, str]:
    """Both-teams-to-score option labels, naming the two real teams (plain text, for buttons)."""
    return {
        BttsSel.BOTH: "Ambas marcam",
        BttsSel.ONLY_HOME: f"Só o {home_team}",
        BttsSel.ONLY_AWAY: f"Só o {away_team}",
        BttsSel.NEITHER: "Nenhuma marca",
    }


OVER_UNDER_LABELS: dict[OverUnderSel, str] = {
    OverUnderSel.OVER: "Mais de 2.5 (3+ gols)",
    OverUnderSel.UNDER: "Menos de 2.5 (até 2 gols)",
}

# Display order for category listings (highest points first).
CATEGORY_ORDER: tuple[BetCategory, ...] = (
    BetCategory.EXACT_SCORE,
    BetCategory.FIRST_TEAM,
    BetCategory.BTTS,
    BetCategory.WINNER,
    BetCategory.OVER_UNDER,
)


_WEEKDAYS_PT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def escape(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(text, quote=False)


def format_money_cents(cents: int, *, currency: str, decimals: int = 2) -> str:
    """Render integer cents as pt-BR money, e.g. ``R$ 90,00`` (comma decimal; no float math)."""
    if decimals <= 0:
        return f"{currency} {cents}"
    scale = 10**decimals
    whole, frac = divmod(cents, scale)
    return f"{currency} {whole},{frac:0{decimals}d}"


def format_kickoff_local(kickoff_local: datetime) -> str:
    """Format a local-time kickoff in pt-BR, e.g. ``Sáb 16/06 16:00``."""
    weekday = _WEEKDAYS_PT[kickoff_local.weekday()]
    return f"{weekday} {kickoff_local:%d/%m %H:%M}"


def format_kickoff_short(kickoff_local: datetime) -> str:
    """Concise kickoff for compact inline buttons, e.g. ``16/06 16:00`` (no weekday)."""
    return f"{kickoff_local:%d/%m %H:%M}"


def announcement_text(games: Sequence[tuple[str, str, datetime]]) -> str:
    """Morning 'next 24h' announcement (§9.1). Each item: ``(home, away, kickoff_local)``."""
    lines = [
        f"• {escape(home)} x {escape(away)} — {format_kickoff_local(kickoff)}"
        for home, away, kickoff in games
    ]
    body = "\n".join(lines)
    return (
        "🐯 <b>Jogos das próximas 24h — apostas abertas!</b>\n\n"
        f"{body}\n\n"
        'Toque em "🎯 Apostar" abaixo para palpitar no privado (fecha no apito inicial).'
    )


def _bettors_line(bettors: Sequence[tuple[str, int]]) -> str:
    """Inline 'who already bet' line for one game (§9.3).

    ``bettors``: ``(display_name, bets_placed)`` in display order. Shows each bettor's count out
    of :data:`TOTAL_CATEGORIES`, e.g. ``Felipe (3/5)``; nudges when nobody has bet yet.
    """
    if not bettors:
        return "👥 Ninguém palpitou ainda 👀"
    listing = ", ".join(f"{escape(name)} ({count}/{TOTAL_CATEGORIES})" for name, count in bettors)
    return f"👥 Já palpitaram: {listing}"


def reminder_text(
    games: Sequence[tuple[str, str, datetime, Sequence[tuple[str, int]]]],
    *,
    tournament_blocks: Sequence[str] | None = None,
) -> str:
    """~1h pre-kickoff betting reminder for one kickoff slot (§9.3).

    Each item: ``(home, away, kickoff_local, bettors)`` where ``bettors`` is an ordered
    ``(display_name, bets_placed)`` list. Combined into a single message when several games share
    the slot; each game line is followed by a ``👥`` line naming who already bet and how many of
    the 5 categories. When ``tournament_blocks`` is given (aligned by index, §22), a non-empty
    entry adds a ``🏆`` line under the game mentioning entrants who still need to bet. Followed by
    one ``🎯 Apostar`` button per game (built separately).
    """
    lines: list[str] = []
    for index, (home, away, kickoff, bettors) in enumerate(games):
        lines.append(f"• {escape(home)} x {escape(away)} — {format_kickoff_local(kickoff)}")
        lines.append(f"  {_bettors_line(bettors)}")
        if tournament_blocks is not None and tournament_blocks[index]:
            lines.append(f"  {tournament_blocks[index]}")
    body = "\n".join(lines)
    return (
        "⏰ <b>Falta ~1h pro apito! Ainda dá pra palpitar:</b>\n\n"
        f"{body}\n\n"
        'Toque em "🎯 Apostar" abaixo para palpitar no privado (fecha no apito inicial).'
    )


def reannounce_text(home: str, away: str, kickoff_local: datetime) -> str:
    """Concise re-announcement after a fixture is rescheduled (bets stay valid; §9.1)."""
    return (
        f"🔄 <b>Jogo remarcado:</b> {escape(home)} x {escape(away)} — "
        f"agora {format_kickoff_local(kickoff_local)}.\n"
        "Seus palpites continuam valendo (agora para o novo horário)."
    )


def void_text(home: str, away: str) -> str:
    """Notice that a fixture was postponed/cancelled and its bets voided (§9.1)."""
    return (
        f"⛔ <b>Jogo cancelado/adiado:</b> {escape(home)} x {escape(away)}.\n"
        "As apostas desse jogo foram anuladas (sem pontos)."
    )


def results_text(
    *,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    first_team_name: str | None,
    players: Sequence[tuple[int, str, int, Sequence[tuple[str, bool, int]]]],
) -> str:
    """Group results message (§8.3). ``players``: (telegram_id, name, total, [(label, ok, pts)])."""
    lines = [f"🏁 <b>{escape(home)} {home_goals} x {away_goals} {escape(away)}</b>"]
    if first_team_name:
        lines.append(f"⚽ Primeira equipe a marcar: {escape(first_team_name)}")
    else:
        lines.append("⚽ Sem gol válido no tempo normal (0 a 0 ou só gol contra)")
    lines.append("")
    if not players:
        lines.append("Ninguém apostou neste jogo. 🙈")
        return "\n".join(lines)
    lines.append("<b>Pontuação do jogo:</b>")
    for telegram_id, name, total, categories in players:
        marks = " · ".join(
            f"{'✓' if ok else '✗'} {label}{f' (+{pts})' if ok else ''}"
            for label, ok, pts in categories
        )
        lines.append(f"{mention(telegram_id, name)} — <b>{total}</b> pts\n  {marks}")
    return "\n".join(lines)


def correction_text(
    *,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    corrected_from: tuple[int, int] | None,
    first_team_name: str | None,
    players: Sequence[tuple[int, str, int, int, Sequence[tuple[str, bool, int]]]],
) -> str:
    """Group post when a settled game is re-graded after a late provider correction (§8.3/§9.2).

    ``corrected_from`` is the previous 90′ score, shown as ``(antes: H x A)`` only when the score
    actually changed (``None`` for a first-scorer/advancing-only correction). ``players`` lists only
    the **affected** bettors as ``(telegram_id, name, old_total, new_total, [(label, ok, pts)])``.
    """
    score_line = f"🏁 <b>{escape(home)} {home_goals} x {away_goals} {escape(away)}</b>"
    if corrected_from is not None:
        prev_home, prev_away = corrected_from
        score_line += f" (antes: {prev_home} x {prev_away})"
    lines = [
        "⚠️ <b>Placar corrigido!</b>",
        "Os pontos deste jogo foram recalculados — confira o /placar.",
        score_line,
    ]
    if first_team_name:
        lines.append(f"⚽ Primeira equipe a marcar: {escape(first_team_name)}")
    else:
        lines.append("⚽ Sem gol válido no tempo normal (0 a 0 ou só gol contra)")
    lines.append("")
    lines.append("<b>Pontuação recalculada:</b>")
    for telegram_id, name, old_total, new_total, categories in players:
        marks = " · ".join(
            f"{'✓' if ok else '✗'} {label}{f' (+{pts})' if ok else ''}"
            for label, ok, pts in categories
        )
        lines.append(
            f"{mention(telegram_id, name)} — <b>{old_total} → {new_total}</b> pts\n  {marks}"
        )
    return "\n".join(lines)


def kickoff_text(home_team: str, away_team: str) -> str:
    """Group post when a tracked game kicks off (§9.4)."""
    return (
        f"🔥 <b>Bola rolando!</b> {escape(home_team)} x {escape(away_team)} "
        "— boa sorte, Tigrinhos! 🐯"
    )


def goal_text(
    *,
    scoring_team: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
) -> str:
    """Group post for one goal: scoring team + running score (§9.4).

    Built straight from the live score feed (``get_live_results``) so it posts the moment the score
    ticks. Scorer/minute/penalty/own-goal detail is intentionally dropped — that needs the slower
    ``/fixtures/events`` feed, which lags the live score by minutes (§9.4).
    """
    return (
        f"⚽ <b>GOL do {escape(scoring_team)}!</b> "
        f"{escape(home_team)} {home_score} x {away_score} {escape(away_team)} 🐯"
    )


def goal_cancelled_text(
    *,
    scoring_team: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
) -> str:
    """Group post when a counted goal is disallowed by VAR (§9.4).

    The live score split tells us *which* team's tally dropped, so the team is always named.
    ``home_score`` / ``away_score`` are the **current** running score after the cancellation.
    """
    return (
        f"🚫 <b>Gol anulado pelo VAR!</b> Era gol do {escape(scoring_team)}.\n"
        f"Placar segue: {escape(home_team)} {home_score} x {away_score} {escape(away_team)} 🐯"
    )


_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def board_text(
    *,
    weekly: bool,
    rows: Sequence[tuple[int, str, int]],
    caller_outside: tuple[int, int] | None = None,
) -> str:
    """Scoreboard message (§10). ``rows``: (rank, name, points); ``caller_outside``: (rank, pts)."""
    title = "🏆 <b>Placar da Semana</b>" if weekly else "🏆 <b>Placar Geral</b>"
    if not rows:
        return f"{title}\n\nAinda não há pontos. Façam seus palpites! 🐯"
    lines = [title, ""]
    for position, name, points in rows:
        marker = _MEDALS.get(position, f"{position}.")
        lines.append(f"{marker} {escape(name)} — <b>{points}</b> pts")
    if caller_outside is not None:
        lines.append("⋯")
        lines.append(f"Você: {caller_outside[0]}º — <b>{caller_outside[1]}</b> pts")
    return "\n".join(lines)


def game_board_text(
    *,
    home: str,
    away: str,
    home_goals: int | None,
    away_goals: int | None,
    rows: Sequence[tuple[int, str, int]],
) -> str:
    """Per-game scoreboard (§10). ``rows``: (rank, name, points) for that one finished game."""
    score = (
        f" {home_goals} x {away_goals} "
        if home_goals is not None and away_goals is not None
        else " x "
    )
    title = f"🏆 <b>Placar do jogo</b>\n{escape(home)}{score}{escape(away)}"
    if not rows:
        return f"{title}\n\nNinguém apostou neste jogo. 🙈"
    lines = [title, ""]
    for position, name, points in rows:
        marker = _MEDALS.get(position, f"{position}.")
        lines.append(f"{marker} {escape(name)} — <b>{points}</b> pts")
    return "\n".join(lines)


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


def mention(telegram_id: int, name: str) -> str:
    """An HTML inline mention that works even without an @username."""
    return f'<a href="tg://user?id={telegram_id}">{escape(name)}</a>'


def describe_bet_value(
    payload: Payload,
    *,
    home_team: str = "Mandante",
    away_team: str = "Visitante",
) -> str:
    """Just the selection of a bet, with no category prefix (HTML-safe; team names escaped).

    e.g. ``2x1`` / ``Brasil`` / ``Ambas marcam``. Used by the kickoff bet reveal (§9.4, grouped
    by category, so the category is already the header) and reused by :func:`describe_bet`.
    """
    if isinstance(payload, ExactScorePayload):
        return f"{payload.home}x{payload.away}"
    if isinstance(payload, WinnerPayload):
        labels = {
            WinnerSel.HOME: escape(home_team),
            WinnerSel.DRAW: "Empate",
            WinnerSel.AWAY: escape(away_team),
        }
        return labels[payload.sel]
    if isinstance(payload, BttsPayload):
        return btts_labels(escape(home_team), escape(away_team))[payload.sel]
    if isinstance(payload, OverUnderPayload):
        return OVER_UNDER_LABELS[payload.sel]
    if isinstance(payload, FirstTeamPayload):
        return escape(home_team) if payload.sel is FirstTeamSel.HOME else escape(away_team)
    assert_never(payload)  # pragma: no cover


def describe_bet(
    payload: Payload,
    *,
    home_team: str = "Mandante",
    away_team: str = "Visitante",
) -> str:
    """Human-readable pt-BR description of a bet (for /minhas_apostas and confirmations)."""
    value = describe_bet_value(payload, home_team=home_team, away_team=away_team)
    if isinstance(payload, ExactScorePayload):
        prefix = "Placar exato"
    elif isinstance(payload, WinnerPayload):
        prefix = "Vencedor"
    elif isinstance(payload, BttsPayload):
        prefix = "Ambas marcam"
    elif isinstance(payload, OverUnderPayload):
        prefix = "Gols"
    else:  # FirstTeamPayload
        prefix = "Primeira equipe a marcar"
    return f"{prefix}: {value}"


def closed_bets_text(
    *,
    home: str,
    away: str,
    items: Sequence[tuple[BetCategory, str, str]],
) -> str | None:
    """Kickoff bet reveal, grouped by category (§9.4) — bets are secret only until kickoff.

    ``items``: ``(category, player_name, value)`` where ``value`` is the already-HTML-safe
    selection string from :func:`describe_bet_value`; player names are escaped here. Categories
    appear in :data:`CATEGORY_ORDER`, only those with at least one bet; players are sorted by name
    within each. Names are plain text, **not** @-mentions — a player repeats across up to five
    categories, so mentions would spam pings. Returns ``None`` when nobody bet (the reveal is then
    skipped — there is nothing to expose).
    """
    if not items:
        return None
    by_category: dict[BetCategory, list[tuple[str, str]]] = {}
    for category, name, value in items:
        by_category.setdefault(category, []).append((name, value))
    lines = [
        "🔒 <b>Apostas fechadas!</b> Confira os palpites:",
        f"⚽ {escape(home)} x {escape(away)}",
    ]
    for category in CATEGORY_ORDER:
        bettors = by_category.get(category)
        if not bettors:
            continue
        lines.append("")
        lines.append(f"<b>{CATEGORY_LABELS[category]}</b>")
        for name, value in sorted(bettors, key=lambda nv: nv[0].casefold()):
            lines.append(f"  • {escape(name)}: {value}")
    return "\n".join(lines)


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
    score = (
        f"{home_goals}x{away_goals}" if home_goals is not None and away_goals is not None else "x"
    )
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


def points_table_text() -> str:
    """The points table, derived from the single source of truth (scoring.POINTS)."""
    lines = [
        f"• {CATEGORY_LABELS[category]}: <b>{POINTS[category]}</b> pts"
        for category in CATEGORY_ORDER
    ]
    return "\n".join(lines)


def category_button_label(category: BetCategory) -> str:
    """Category picker button label with its point value, e.g. ``Placar exato · 5 pts``.

    Plain text (inline-button labels don't parse HTML); points come from scoring.POINTS.
    """
    points = POINTS[category]
    unit = "pt" if points == 1 else "pts"
    return f"{CATEGORY_LABELS[category]} · {points} {unit}"


def welcome_text() -> str:
    """`/start` with no payload — short welcome pointing to /ajuda."""
    return (
        "🐯 <b>Bem-vindo ao Tigrinho da Copa!</b>\n\n"
        "Aqui a gente palpita nos jogos da Copa do Mundo 2026 — sem dinheiro, só pela glória. "
        "Os palpites são feitos no privado e o grupo recebe os anúncios, resultados e o placar.\n\n"
        "Use /apostar para começar e /ajuda para entender as regras."
    )


def help_text() -> str:
    """`/ajuda` — full pt-BR explanation (§11)."""
    return (
        "🐯 <b>Tigrinho da Copa — Como funciona</b>\n\n"
        "Bolão de palpites da <b>Copa do Mundo 2026</b>, sem dinheiro, entre amigos do grupo. "
        "O bot anuncia os jogos no <b>grupo</b>; você <b>aposta no privado</b> com o bot "
        "(toque em <b>🎯 Apostar</b> no anúncio, ou mande /apostar aqui). Os palpites ficam "
        "secretos até o apito inicial — quando a bola rola, o grupo vê os palpites de todos.\n\n"
        "<b>Comandos</b>\n"
        "• /apostar — abrir o assistente de palpites (no privado)\n"
        "• /minhas_apostas — ver seus palpites: em aberto e em andamento na hora, e os "
        "encerrados resumidos (toque em 📜 Ver encerrados para o histórico, jogo a jogo) "
        "(no privado)\n"
        "• /jogos — próximos jogos e o que falta palpitar\n"
        "• /placar — ranking (Geral e da Semana)\n"
        "• /placar_jogo — placar de um jogo já encerrado\n"
        "• /placar_jogos — placar somando vários jogos encerrados\n"
        "• /palpite — escolha um jogo (em andamento ou nas próximas 24h) e veja o palpite da "
        "IA (Gemini)\n"
        "• /bolaozinhos — ver os bolãozinhos (competições com prêmio em dinheiro)\n"
        "• /bolaozinho_placar — placar parcial de um bolãozinho\n"
        "• /bolaozinho_participantes — ver quem entrou num bolãozinho\n"
        "• /entrar — entrar num bolãozinho\n"
        "• /bolaozinho_criar — criar um bolãozinho (<code>Nome | preço</code>)\n"
        "• /bolaozinho_cancelar — cancelar um bolãozinho (<code>id [motivo]</code>)\n"
        "• /vincular_splitwise — vincular sua conta do Splitwise (pro acerto do prêmio)\n"
        "• /ajuda — esta mensagem\n"
        "• /start — boas-vindas\n\n"
        "<b>Categorias de aposta</b> (uma por categoria por jogo, editável até o apito):\n"
        "• <b>Placar exato</b> — ex.: 2x1\n"
        "• <b>Primeira equipe a marcar</b> — uma das duas seleções do jogo\n"
        "• <b>Ambas marcam</b> — Ambas / Só a 1ª seleção / Só a 2ª seleção / Nenhuma\n"
        "• <b>Vencedor</b> — uma das seleções ou Empate\n"
        "• <b>Mais/Menos 2.5 gols</b> — Mais (3+) ou Menos (até 2)\n\n"
        "<b>Pontuação</b>\n"
        f"{points_table_text()}\n\n"
        "<b>Regras importantes</b>\n"
        "• Tudo é avaliado pelo resultado dos <b>90 minutos</b> (sem prorrogação/pênaltis).\n"
        "• <b>Mata-mata:</b> o vencedor é quem <b>avança</b> (quem passou de fase). Não existe "
        "empate no mata-mata — a opção <i>Empate</i> nem aparece.\n"
        "• <b>Primeira equipe a marcar:</b> gol contra não conta; em 0 a 0 (ou só gol contra), "
        "todos que apostaram nessa categoria perdem.\n"
        "• As apostas <b>fecham no apito inicial</b> de cada jogo.\n\n"
        "<b>Bolãozinhos</b> (com prêmio em dinheiro 💰)\n"
        "• Um <b>bolãozinho</b> é uma competição sobre um conjunto de jogos, com uma "
        "<b>entrada</b> (ex.: R$ 10). Qualquer um cria com "
        "<code>/bolaozinho_criar Nome | preço</code> e adiciona jogos que ainda não começaram. "
        "Quem criou (ou o admin) gerencia o bolãozinho.\n"
        "• Quando um bolãozinho abre, eu <b>aviso todo mundo no privado</b> (e marco o grupo). "
        "Use <b>/entrar</b> (ou o botão <b>🏆 Entrar</b> no anúncio/aviso) pra participar — "
        "eu mando os jogos no <b>privado</b> pra palpitar. As entradas <b>fecham quando o primeiro "
        "jogo começa</b>, e os palpites valem os mesmos pontos de sempre.\n"
        "• Quem criou pode cancelar com <b>/bolaozinho_cancelar id [motivo]</b>; aviso todo mundo "
        "que entrou no privado, com o motivo.\n"
        "• <b>Prêmio = pote − uma entrada</b> (você não ganha a sua própria entrada de volta): com "
        "10 pessoas a R$ 10, o pote é R$ 100 e o prêmio R$ 90. Vence quem fizer mais pontos nos "
        "jogos do bolãozinho; <b>empate divide o prêmio</b> igualmente.\n"
        "• A cada jogo do bolãozinho que <b>termina</b>, o grupo recebe o <b>placar parcial</b> "
        "(quem está na frente até ali). Você também pode ver a qualquer momento com "
        "<b>/bolaozinho_placar</b>.\n"
        "• Quando todos os jogos terminam, o bot anuncia o vencedor e quanto leva. O acerto do "
        "dinheiro é por fora — o bot só faz a conta.\n"
        "• <b>Splitwise:</b> se o grupo usa Splitwise, vincule sua conta com "
        "<b>/vincular_splitwise</b> (uma vez só). Quando um bolãozinho termina, eu registro o "
        "acerto lá automaticamente — quem perdeu deve a entrada, quem ganhou recebe.\n\n"
        "Boa sorte! 🍀"
    )


def palpite_text(
    *,
    home: str,
    away: str,
    kickoff_local: datetime,
    analysis: str,
    payloads: Sequence[Payload],
    curiosity: str,
) -> str:
    """One game's AI palpite (§20): header, analysis, a line per category, optional curiosity."""
    lines = [
        f"🤖 <b>Palpite da IA</b> — {escape(home)} x {escape(away)}",
        f"🗓 {format_kickoff_local(kickoff_local)}",
        "",
    ]
    if analysis:
        lines.append(f"📊 {escape(analysis)}")
        lines.append("")
    lines.extend(f"• {describe_bet(p, home_team=home, away_team=away)}" for p in payloads)
    if curiosity:
        lines.append("")
        lines.append(f"💡 <b>Curiosidade:</b> {escape(curiosity)}")
    lines.append("")
    lines.append("<i>Gerado por IA com busca na web — sem garantias. 🐯</i>")
    return "\n".join(lines)


def palpite_pick_text() -> str:
    """`/palpite` prompt — pick an eligible game (live or next-24h) to see its AI palpite (§20)."""
    return "🤖 Escolha um jogo para ver o palpite da IA:"


def palpite_no_key_text() -> str:
    """Shown when /palpite runs but no Gemini key is configured (§20)."""
    return (
        "🤖 <b>Palpite da IA indisponível</b>\n\n"
        "Nenhuma chave do Gemini foi configurada. Para habilitar os palpites da IA, adicione "
        "<code>GEMINI_API_KEY</code> ao arquivo <code>.env</code> e reinicie o bot."
    )


def palpite_no_games_text() -> str:
    """Shown when /palpite runs but no game is in progress or kicks off within 24h (§20)."""
    return "🤖 Nenhum jogo em andamento ou nas próximas 24h para palpitar. 🐯"


def palpite_working_text() -> str:
    """Sent while the (slow) grounded Gemini analysis runs (§20)."""
    return "🧠 Analisando os jogos com a IA (busca na web)… isso pode levar um minutinho."


def palpite_generating_text() -> str:
    """Shown when a generation is already in progress (avoid duplicate AI requests; §20)."""
    return "🧠 Já estou analisando os jogos. Aguarde um instante e toque no jogo de novo. 🐯"


def palpite_error_text() -> str:
    """Shown when the AI palpite generation fails (§20)."""
    return "🤖 Não consegui gerar os palpites agora. Tente de novo mais tarde. 🐯"


# --- Bolãozinhos (tournaments, Feature 7 / §22) -----------------------------------------------

_TOURNAMENT_STATUS_LABELS: dict[TournamentStatus, str] = {
    TournamentStatus.DRAFT: "rascunho",
    TournamentStatus.OPEN: "aberto",
    TournamentStatus.FINISHED: "encerrado",
    TournamentStatus.CANCELLED: "cancelado",
}


def tournament_status_label(status: TournamentStatus) -> str:
    """pt-BR label for a bolãozinho status."""
    return _TOURNAMENT_STATUS_LABELS[status]


def _entries_word(n: int) -> str:
    return "entrada" if n == 1 else "entradas"


def _game_line(home: str, away: str, kickoff_local: datetime) -> str:
    return f"• {escape(home)} x {escape(away)} — {format_kickoff_local(kickoff_local)}"


def tournament_result_text(
    *,
    name: str,
    n_entrants: int,
    pot_cents: int,
    prize_cents: int,
    winners: Sequence[tuple[int, str, int]],
    per_winner_cents: int,
    remainder_cents: int,
    is_correction: bool,
    currency: str,
    decimals: int = 2,
) -> str:
    """The group winner/payout post (``winners`` = (telegram_id, name, score)); §7."""

    def money(cents: int) -> str:
        return format_money_cents(cents, currency=currency, decimals=decimals)

    safe_name = escape(name)
    header = (
        f'⚠️ Resultado do bolãozinho "{safe_name}" corrigido!'
        if is_correction
        else f'🏆 Bolãozinho "{safe_name}" encerrado!'
    )
    lines = [
        header,
        f"Pote: {money(pot_cents)} ({n_entrants} {_entries_word(n_entrants)}) · "
        f"Prêmio: {money(prize_cents)}",
        "",
    ]
    sobra = f" (sobra {money(remainder_cents)})" if remainder_cents else ""
    if len(winners) == 1:
        telegram_id, winner_name, score = winners[0]
        lines.append(f"🥇 Vencedor: {mention(telegram_id, winner_name)} — {score} pts")
        lines.append(f"Leva {money(per_winner_cents)}{sobra}")
    else:
        score = winners[0][2]
        lines.append(f"🥇 Empate ({len(winners)}) — {score} pts cada:")
        lines.append("  ".join(f"• {mention(tid, nm)}" for tid, nm, _ in winners))
        lines.append(f"Cada um leva {money(per_winner_cents)}{sobra}")
    return "\n".join(lines)


def tournament_no_result_text(*, name: str) -> str:
    """The group "no scorable result" notice (all games void, or no entrants); §7."""
    return (
        f'🏁 Bolãozinho "{escape(name)}" encerrado — sem resultado '
        "(nenhum jogo valeu ou ninguém entrou)."
    )


def tournament_standings_text(
    *,
    name: str,
    settled_count: int,
    total_games: int,
    n_entrants: int,
    pot_cents: int,
    prize_cents: int,
    standings: Sequence[tuple[str, int]],
    currency: str,
    decimals: int = 2,
    is_final: bool = False,
    with_hint: bool = True,
) -> str:
    """The bolãozinho placar — standings so far (§22.4). Shared by the auto-post and the command.

    Posted to the group once per newly-finished member game (``with_hint`` points to the command);
    also rendered on demand by ``/bolaozinho_placar`` (``with_hint=False``). ``standings`` is the
    already-ranked ``(display_name, points)`` list; names are plain (not @-mentions) so a placar
    posted many times never spams pings (same choice as /placar)."""

    def money(cents: int) -> str:
        return format_money_cents(cents, currency=currency, decimals=decimals)

    header = "🏁 Placar final" if is_final else "📊 Placar parcial"
    lines = [
        f'{header} — bolãozinho "<b>{escape(name)}</b>"',
        f"{settled_count}/{total_games} jogos · {n_entrants} {_entries_word(n_entrants)} · "
        f"Pote {money(pot_cents)} · Prêmio {money(prize_cents)}",
        "",
    ]
    if standings:
        for position, (player_name, points) in enumerate(standings, start=1):
            marker = _MEDALS.get(position, f"{position}.")
            lines.append(f"{marker} {escape(player_name)} — <b>{points}</b> pts")
    else:
        lines.append("Ninguém pontuou ainda. 🐯")
    if with_hint:
        lines.append("")
        lines.append("📲 Acompanhe o placar com /bolaozinho_placar")
    return "\n".join(lines)


def tournament_cancelled_dm_text(*, name: str, reason: str | None = None) -> str:
    """DM sent to each entrant when a bolãozinho is cancelled (with the reason, if any); §22."""
    base = f"❌ O bolãozinho <b>{escape(name)}</b> foi cancelado."
    if reason:
        return f"{base}\nMotivo: {escape(reason)}"
    return base


def tournament_announcement_text(
    *,
    name: str,
    entry_price_cents: int,
    games: Sequence[tuple[str, str, datetime]],
    currency: str,
    decimals: int = 2,
    mentions: Sequence[tuple[int, str]] = (),
) -> str:
    """The group "novo bolãozinho" publish post (deep-link buttons added by the keyboard); §5/§22.

    ``mentions`` (telegram_id, name) are @-mentioned in the same message so everyone the bot knows
    is pinged about the new bolãozinho.
    """
    price = format_money_cents(entry_price_cents, currency=currency, decimals=decimals)
    lines = [
        f"🏆 Novo bolãozinho: <b>{escape(name)}</b> — entrada {price}",
        f"{len(games)} jogo(s):",
        *[_game_line(home, away, kickoff) for home, away, kickoff in games],
        "",
        "Use /entrar para participar! 🐯",
    ]
    if mentions:
        pings = " ".join(mention(telegram_id, person) for telegram_id, person in mentions)
        lines.append(f"\n📣 {pings}")
    return "\n".join(lines)


def tournament_open_dm_text(
    *,
    name: str,
    entry_price_cents: int,
    games: Sequence[tuple[str, str, datetime]],
    currency: str,
    decimals: int = 2,
) -> str:
    """DM broadcast to everyone the bot knows when a bolãozinho opens (§22.3).

    Mirrors the group announcement so each known player also gets a personal ping; the deep-link
    **Entrar** button is added by the keyboard. Telegram can't message users who never started the
    bot, so unreachable players are skipped — they still see the @-mention in the group post.
    """
    price = format_money_cents(entry_price_cents, currency=currency, decimals=decimals)
    lines = [
        f"🏆 Abriu um novo bolãozinho: <b>{escape(name)}</b> — entrada {price}",
        f"{len(games)} jogo(s):",
        *[_game_line(home, away, kickoff) for home, away, kickoff in games],
        "",
        "Toque em <b>Entrar no bolãozinho</b> aqui embaixo pra participar! 🐯",
    ]
    return "\n".join(lines)


# --- Splitwise linking + registration (Feature 8 / §23) ------------------------------------------
def splitwise_link_intro_text() -> str:
    """The first wizard step: ask whether the player is already in the Splitwise group (§23)."""
    return (
        "💸 <b>Vincular Splitwise</b>\n\n"
        "Pra eu registrar os acertos do bolãozinho no Splitwise, preciso saber quem é você lá.\n\n"
        "<b>Você já está no grupo do Splitwise?</b>"
    )


def splitwise_ask_email_text() -> str:
    """Prompt for the email when the player is not yet in the group (the "Não" branch)."""
    return (
        "Beleza! Me manda o <b>e-mail</b> que você usa (ou vai usar) no Splitwise, que eu te "
        "adiciono ao grupo. 📧"
    )


def splitwise_linked_text(*, member_name: str) -> str:
    """Confirmation after a successful link."""
    return (
        f"✅ Splitwise vinculado a <b>{escape(member_name)}</b>. "
        "Agora é só entrar nos bolãozinhos! 🐯"
    )


def splitwise_invalid_email_text() -> str:
    """Rejection of a malformed email (keeps the wizard waiting)."""
    return "Hmm, isso não parece um e-mail válido. Tenta de novo? 📧"


def splitwise_link_required_text() -> str:
    """Join-guard rejection: an AUTO bolãozinho needs the player linked first (§23)."""
    return (
        "🔗 Antes de entrar, <b>vincule seu Splitwise</b> (é rapidinho) — assim eu consigo "
        "registrar o acerto do prêmio. Toque no botão aqui embaixo."
    )


def splitwise_all_linked_text() -> str:
    """Shown when every group member is already linked to a Telegram player."""
    return (
        "Todo mundo do grupo do Splitwise já está vinculado por aqui. Se você não está no grupo "
        "ainda, escolha <b>Não estou no grupo</b>."
    )


def splitwise_not_configured_text() -> str:
    """Shown when the feature is disabled (no key/group)."""
    return "O Splitwise não está configurado neste bot."


def splitwise_expense_description(*, name: str, winners: Sequence[str]) -> str:
    """The Splitwise expense description for a finished bolãozinho's result."""
    who = ", ".join(winners) if winners else "—"
    return f"🏆 Bolãozinho '{name}' — {who}"


def splitwise_admin_ready_text(*, tournament_id: int, name: str) -> str:
    """DM to the admin when a MANUAL bolãozinho is fully linked and ready to register (§23)."""
    return (
        f"💸 Bolãozinho #{tournament_id} (<b>{escape(name)}</b>) já está com todo mundo vinculado "
        "no Splitwise. Use /bolaozinho_splitwise pra registrar o acerto."
    )


def tournament_card_text(
    *,
    tournament_id: int,
    name: str,
    status: TournamentStatus,
    entry_price_cents: int,
    games: Sequence[tuple[str, str, datetime]],
    n_entrants: int,
    currency: str,
    decimals: int = 2,
) -> str:
    """The creator's management card; §5."""
    price = format_money_cents(entry_price_cents, currency=currency, decimals=decimals)
    lines = [
        f"🐯 Bolãozinho #{tournament_id} — <b>{escape(name)}</b>",
        f"Status: {tournament_status_label(status)} · Entrada: {price} · "
        f"{n_entrants} {_entries_word(n_entrants)}",
        f"Jogos ({len(games)}):",
    ]
    if games:
        lines += [_game_line(home, away, kickoff) for home, away, kickoff in games]
    else:
        lines.append("• Nenhum jogo ainda.")
    return "\n".join(lines)


def tournament_list_text(
    items: Sequence[tuple[int, str, TournamentStatus, int, int]],
    *,
    currency: str,
    decimals: int = 2,
) -> str:
    """List of bolãozinhos: items = (id, name, status, pot_cents, n_entrants); §5."""
    if not items:
        return "🐯 Nenhum bolãozinho ainda. Crie um com /bolaozinho_criar."
    lines = ["🐯 <b>Bolãozinhos</b>"]
    for tournament_id, name, status, pot_cents, n_entrants in items:
        pot = format_money_cents(pot_cents, currency=currency, decimals=decimals)
        lines.append(
            f"• #{tournament_id} {escape(name)} — {tournament_status_label(status)} · "
            f"pote {pot} · {n_entrants} {_entries_word(n_entrants)}"
        )
    return "\n".join(lines)


def tournament_details_text(
    *,
    tournament_id: int,
    name: str,
    status: TournamentStatus,
    entry_price_cents: int,
    pot_cents: int,
    prize_cents: int,
    n_entrants: int,
    games: Sequence[tuple[str, str, datetime, int | None, int | None]],
    standings: Sequence[tuple[str, int]],
    you_entered: bool,
    currency: str,
    decimals: int = 2,
) -> str:
    """Public details + live mini-standings; games = (home,away,kickoff,home_goals,away_goals)."""

    def money(cents: int) -> str:
        return format_money_cents(cents, currency=currency, decimals=decimals)

    lines = [
        f"🐯 Bolãozinho #{tournament_id} — <b>{escape(name)}</b>",
        f"Status: {tournament_status_label(status)} · Entrada: {money(entry_price_cents)}",
        f"Pote: {money(pot_cents)} ({n_entrants} {_entries_word(n_entrants)}) · "
        f"Prêmio: {money(prize_cents)}",
        "Jogos:",
    ]
    for home, away, kickoff, home_goals, away_goals in games:
        if home_goals is not None and away_goals is not None:
            lines.append(f"• {escape(home)} {home_goals}x{away_goals} {escape(away)}")
        else:
            lines.append(_game_line(home, away, kickoff))
    if standings:
        lines.append("")
        lines.append("📊 Parcial:")
        for rank, (player_name, points) in enumerate(standings, start=1):
            lines.append(f"{rank}. {escape(player_name)} — {points} pts")
    lines.append("")
    lines.append("✅ Você está participando." if you_entered else "Use /entrar para participar.")
    return "\n".join(lines)


def entry_card_text(
    *,
    name: str,
    entry_price_cents: int,
    pot_cents: int,
    prize_cents: int,
    n_entrants: int,
    games: Sequence[tuple[str, str, datetime]],
    currency: str,
    decimals: int = 2,
) -> str:
    """The /entrar confirmation card shown before joining; §5."""

    def money(cents: int) -> str:
        return format_money_cents(cents, currency=currency, decimals=decimals)

    lines = [
        f"🏆 <b>{escape(name)}</b>",
        f"Entrada: {money(entry_price_cents)} · Pote atual: {money(pot_cents)} "
        f"({n_entrants} {_entries_word(n_entrants)}) · Prêmio: {money(prize_cents)}",
        f"Jogos ({len(games)}):",
        *[_game_line(home, away, kickoff) for home, away, kickoff in games],
    ]
    return "\n".join(lines)


def entry_confirmed_text(
    *,
    name: str,
    n_entrants: int,
    pot_cents: int,
    prize_cents: int,
    currency: str,
    decimals: int = 2,
) -> str:
    """Shown right after a successful /entrar; §5."""

    def money(cents: int) -> str:
        return format_money_cents(cents, currency=currency, decimals=decimals)

    return (
        f"✅ Você entrou no bolãozinho <b>{escape(name)}</b>!\n"
        f"Pote: {money(pot_cents)} ({n_entrants} {_entries_word(n_entrants)}) · "
        f"Prêmio: {money(prize_cents)}\n\n"
        "Agora faça seus palpites nos jogos abaixo 👇 (as apostas fecham no apito inicial)."
    )


def tournament_participants_text(
    *,
    name: str,
    participants: Sequence[str],
    pot_cents: int,
    prize_cents: int,
    currency: str,
    decimals: int = 2,
) -> str:
    """`/bolaozinho_participantes` — who has entered a bolãozinho (§22)."""

    def money(cents: int) -> str:
        return format_money_cents(cents, currency=currency, decimals=decimals)

    n = len(participants)
    header = f"👥 Participantes do bolãozinho <b>{escape(name)}</b> ({n})"
    if not participants:
        return f"{header}\nNinguém entrou ainda. Use /entrar pra ser o primeiro! 🐯"
    lines = [header]
    lines += [f"{rank}. {escape(person)}" for rank, person in enumerate(participants, start=1)]
    lines.append(f"\nPote: {money(pot_cents)} · Prêmio: {money(prize_cents)}")
    return "\n".join(lines)
