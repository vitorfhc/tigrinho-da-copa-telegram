"""Prompt construction for the Gemini AI palpite (COMPLETION.md §20).

PURE: builds the system instruction + user content from a list of fixtures. The system
instruction pins the model to the project's grading rules (everything is judged on the 90′
result, knockout has no draw, "first team" ignores own goals, over = 3+ goals) so the AI's
palpite is consistent with how human bets are scored, and demands a single validated JSON
object. Grounding (Google Search) is enabled by the caller, not the prompt — but the prompt
tells the model to use up-to-date web information.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from tigrinho.domain.text_pt import format_kickoff_local
from tigrinho.enums import Stage


@dataclass(frozen=True, slots=True)
class GameInfo:
    """The minimal fixture facts the model needs to analyze one game."""

    fixture_id: int
    home_team: str
    away_team: str
    kickoff_local: datetime
    stage: Stage


_SYSTEM_INSTRUCTION = """\
Você é um analista profissional de futebol especializado na Copa do Mundo FIFA 2026. \
Pense profundamente e faça uma análise completa de CADA jogo: forma recente das seleções, \
desfalques e lesões, prováveis escalações, histórico de confrontos, importância do jogo e \
local da partida. Use a Pesquisa Google (web) para obter as informações MAIS RECENTES — não \
confie apenas na memória.

Para cada jogo, dê o seu melhor palpite em CADA categoria de aposta. As regras de avaliação \
(siga-as ao palpitar):
- Tudo é avaliado pelo resultado dos 90 minutos (sem prorrogação nem pênaltis).
- exact_score: placar exato no fim dos 90 minutos.
- first_team (primeira equipe a marcar): HOME (mandante) ou AWAY (visitante). Gol contra não \
conta; em 0 a 0 ninguém acerta — ainda assim escolha a equipe mais provável de marcar primeiro.
- btts (ambas marcam): BOTH, ONLY_HOME, ONLY_AWAY ou NEITHER.
- winner (vencedor): HOME, DRAW ou AWAY nos jogos de fase de grupos. Em jogos de MATA-MATA \
NÃO existe empate — escolha HOME ou AWAY (a equipe que avança); nunca use DRAW no mata-mata.
- over_under: OVER se você prevê 3 ou mais gols no total; UNDER se prevê 2 ou menos.

Responda com APENAS UM objeto JSON (sem texto fora do JSON, sem markdown), neste formato exato:
{
  "palpites": [
    {
      "fixture_id": <int, exatamente o id informado>,
      "analysis": "<sua análise em pt-BR, 2 a 4 frases>",
      "exact_score": {"home": <int 0-99>, "away": <int 0-99>},
      "first_team": "HOME" | "AWAY",
      "btts": "BOTH" | "ONLY_HOME" | "ONLY_AWAY" | "NEITHER",
      "winner": "HOME" | "DRAW" | "AWAY",
      "over_under": "OVER" | "UNDER",
      "confidence": <int 0-100, sua confiança geral no palpite>
    }
  ]
}
Inclua exatamente um item por jogo informado, ecoando o fixture_id correspondente."""


def _stage_label(stage: Stage) -> str:
    return "mata-mata" if stage is Stage.KNOCKOUT else "fase de grupos"


def build_palpite_prompt(games: Sequence[GameInfo]) -> tuple[str, str]:
    """Return ``(system_instruction, user_content)`` for the games to analyze."""
    lines = [
        f"- fixture_id={g.fixture_id} | {g.home_team} x {g.away_team} | "
        f"{format_kickoff_local(g.kickoff_local)} | {_stage_label(g.stage)}"
        for g in games
    ]
    user_content = (
        "Analise os seguintes jogos da Copa do Mundo 2026 e dê seus palpites:\n\n"
        + "\n".join(lines)
    )
    return _SYSTEM_INSTRUCTION, user_content
