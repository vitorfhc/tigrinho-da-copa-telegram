"""Wire format + prompt for the daily-bolãozinho game-interest scorer (§24).

Binary-only: the model grades six yes/no criteria per game and proposes a pool name. The numeric
interest score is computed elsewhere (``tigrinho.domain.daily_bolao.interest``) — the prompt
forbids the model from emitting any number or ranking. JSON is requested in the prompt and
validated here (mirrors ``ai/schemas.py``); ``name`` is optional so a missing name degrades to a
deterministic fallback rather than failing the whole pool.
"""

from __future__ import annotations

from collections.abc import Sequence

from tigrinho.ai.prompt import GameInfo
from tigrinho.ai.schemas import _AiModel, extract_json, strip_citation_tags
from tigrinho.domain.text_pt import format_kickoff_local
from tigrinho.enums import Stage

_MAX_NAME_LEN = 60


class GameInterestCriteria(_AiModel):
    """Six yes/no grades for one game (all booleans; the only thing the model scores)."""

    decisive: bool
    quality_matchup: bool
    rivalry_or_storyline: bool
    star_power: bool
    competitive_balance: bool
    goal_potential: bool


class GameInterestScore(_AiModel):
    """One game's binary grades, echoing the requested ``fixture_id``."""

    fixture_id: int
    criteria: GameInterestCriteria


class DailyBolaoScoring(_AiModel):
    """The model's full response: a proposed pool name + per-game binary grades."""

    name: str = ""
    scores: list[GameInterestScore]


def sanitize_name(raw: str) -> str:
    """Clean an untrusted model-proposed name: drop citation tags, collapse whitespace, cap length.

    HTML safety is NOT done here — rendering escapes dynamic strings like everywhere else. Returns
    ``""`` when nothing usable remains, so the caller substitutes the dated fallback.
    """
    cleaned = strip_citation_tags(raw)
    cleaned = " ".join(cleaned.split())
    return cleaned[:_MAX_NAME_LEN].strip()


def parse_scoring(text: str) -> DailyBolaoScoring:
    """Extract + validate a model response into DailyBolaoScoring (raises on bad data)."""
    return DailyBolaoScoring.model_validate_json(extract_json(text))


_SYSTEM_INSTRUCTION = """\
Você é um analista profissional de futebol especializado na Copa do Mundo FIFA 2026. \
Para CADA jogo informado, avalie SEIS critérios binários (sim/não) sobre o quão interessante \
seria apostar nele. Use a Pesquisa Google (web) para informações recentes (forma, importância, \
escalações). NÃO dê nenhuma nota numérica nem ranqueamento — apenas os booleanos.

Critérios (cada um true ou false):
- decisive: é mata-mata, ou o resultado decide classificação/posição no grupo.
- quality_matchup: as duas seleções são fortes / de destaque.
- rivalry_or_storyline: há rivalidade histórica ou um enredo marcante.
- star_power: é provável que um craque mundialmente famoso entre em campo.
- competitive_balance: jogo equilibrado, difícil de prever.
- goal_potential: tende a ser aberto / com muitos gols.

Proponha também UM nome curto e divertido (pt-BR) para um bolão do dia sobre os melhores jogos.

Responda com APENAS UM objeto JSON (sem texto fora do JSON, sem markdown), neste formato exato:
{
  "name": "<nome curto e divertido em pt-BR>",
  "scores": [
    {
      "fixture_id": <int, exatamente o id informado>,
      "criteria": {
        "decisive": <bool>,
        "quality_matchup": <bool>,
        "rivalry_or_storyline": <bool>,
        "star_power": <bool>,
        "competitive_balance": <bool>,
        "goal_potential": <bool>
      }
    }
  ]
}
Inclua exatamente um item por jogo informado, ecoando o fixture_id correspondente."""


def _stage_label(stage: Stage) -> str:
    return "mata-mata" if stage is Stage.KNOCKOUT else "fase de grupos"


def build_scoring_prompt(games: Sequence[GameInfo]) -> tuple[str, str]:
    """Return ``(system_instruction, user_content)`` for grading the day's games."""
    lines = [
        f"- fixture_id={g.fixture_id} | {g.home_team} x {g.away_team} | "
        f"{format_kickoff_local(g.kickoff_local)} | {_stage_label(g.stage)}"
        for g in games
    ]
    user_content = (
        "Avalie os seguintes jogos da Copa do Mundo 2026 (um item por jogo):\n\n" + "\n".join(lines)
    )
    return _SYSTEM_INSTRUCTION, user_content
