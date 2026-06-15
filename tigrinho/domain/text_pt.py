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

CATEGORY_LABELS: dict[BetCategory, str] = {
    BetCategory.EXACT_SCORE: "Placar exato",
    BetCategory.FIRST_TEAM: "Primeira equipe a marcar",
    BetCategory.BTTS: "Ambas marcam",
    BetCategory.WINNER: "Vencedor",
    BetCategory.OVER_UNDER: "Mais/Menos 2.5 gols",
}

BTTS_LABELS: dict[BttsSel, str] = {
    BttsSel.BOTH: "Ambas marcam",
    BttsSel.ONLY_HOME: "Só o mandante",
    BttsSel.ONLY_AWAY: "Só o visitante",
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


def format_kickoff_local(kickoff_local: datetime) -> str:
    """Format a local-time kickoff in pt-BR, e.g. ``Sáb 16/06 16:00``."""
    weekday = _WEEKDAYS_PT[kickoff_local.weekday()]
    return f"{weekday} {kickoff_local:%d/%m %H:%M}"


def announcement_text(games: Sequence[tuple[str, str, datetime]]) -> str:
    """Consolidated 'new games open' announcement (§9.1). Each item: (home, away, kickoff_local)."""
    lines = [
        f"• {escape(home)} x {escape(away)} — {format_kickoff_local(kickoff)}"
        for home, away, kickoff in games
    ]
    body = "\n".join(lines)
    return (
        "🐯 <b>Novos jogos abertos para apostas!</b>\n\n"
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


def mention(telegram_id: int, name: str) -> str:
    """An HTML inline mention that works even without an @username."""
    return f'<a href="tg://user?id={telegram_id}">{escape(name)}</a>'


def describe_bet(
    payload: Payload,
    *,
    home_team: str = "Mandante",
    away_team: str = "Visitante",
) -> str:
    """Human-readable pt-BR description of a bet (for /minhas_apostas and confirmations)."""
    if isinstance(payload, ExactScorePayload):
        return f"Placar exato: {payload.home}x{payload.away}"
    if isinstance(payload, WinnerPayload):
        labels = {
            WinnerSel.HOME: escape(home_team),
            WinnerSel.DRAW: "Empate",
            WinnerSel.AWAY: escape(away_team),
        }
        return f"Vencedor: {labels[payload.sel]}"
    if isinstance(payload, BttsPayload):
        return f"Ambas marcam: {BTTS_LABELS[payload.sel]}"
    if isinstance(payload, OverUnderPayload):
        return f"Gols: {OVER_UNDER_LABELS[payload.sel]}"
    if isinstance(payload, FirstTeamPayload):
        team = escape(home_team) if payload.sel is FirstTeamSel.HOME else escape(away_team)
        return f"Primeira equipe a marcar: {team}"
    assert_never(payload)  # pragma: no cover


def points_table_text() -> str:
    """The points table, derived from the single source of truth (scoring.POINTS)."""
    lines = [
        f"• {CATEGORY_LABELS[category]}: <b>{POINTS[category]}</b> pts"
        for category in CATEGORY_ORDER
    ]
    return "\n".join(lines)


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
        "secretos até o apito inicial.\n\n"
        "<b>Comandos</b>\n"
        "• /apostar — abrir o assistente de palpites (no privado)\n"
        "• /minhas_apostas — ver e apagar seus palpites (no privado)\n"
        "• /jogos — próximos jogos e o que falta palpitar\n"
        "• /placar — ranking (Geral e da Semana)\n"
        "• /ajuda — esta mensagem\n"
        "• /start — boas-vindas\n\n"
        "<b>Categorias de aposta</b> (uma por categoria por jogo, editável até o apito):\n"
        "• <b>Placar exato</b> — ex.: 2x1\n"
        "• <b>Primeira equipe a marcar</b> — Mandante ou Visitante\n"
        "• <b>Ambas marcam</b> — Ambas / Só mandante / Só visitante / Nenhuma\n"
        "• <b>Vencedor</b> — Mandante / Empate / Visitante\n"
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
        "Boa sorte! 🍀"
    )
