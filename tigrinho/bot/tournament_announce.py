"""Post bolãozinho result/correction announcements to the group (Feature 7 / §22, §7).

Shared by every path that can finish or correct a bolãozinho (poll, reconcile, sync, sweep). Sends
are best-effort: a failure logs + DMs the admin and never crashes the bot (§14). Result corrections
are capped per bolãozinho via ``app_context.tournament_corrections`` so an oscillating re-grade
cannot spam the group with contradictory winners (mirrors the reconcile correction cap, §8.3).
"""

from __future__ import annotations

from collections.abc import Sequence

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.runtime import AppContext
from tigrinho.domain.text_pt import escape, tournament_no_result_text, tournament_result_text
from tigrinho.logging import get_logger
from tigrinho.tournament_service import (
    TournamentAnnouncement,
    TournamentNoResultAnnouncement,
    TournamentWinnerAnnouncement,
    on_game_resolved,
)

_log = get_logger("tigrinho.tournament_announce")

# Most automatic group corrections per bolãozinho; beyond it the re-grade is silent + DMs the admin.
CORRECTION_POST_CAP = 2


async def _post_group(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, text: str, *, what: str
) -> bool:
    try:
        await context.bot.send_message(
            chat_id=app_context.settings.group_chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except TelegramError as exc:
        _log.error("tournament_post_failed", what=what, error=str(exc))
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Falha ao postar {what} no grupo: <code>{escape(str(exc))}</code>",
        )
        return False
    return True


def _winner_text(app_context: AppContext, ann: TournamentWinnerAnnouncement) -> str:
    settings = app_context.settings
    return tournament_result_text(
        name=ann.name,
        n_entrants=ann.n_entrants,
        pot_cents=ann.pot_cents,
        prize_cents=ann.prize_cents,
        winners=[(w.telegram_id, w.display_name, w.score) for w in ann.winners],
        per_winner_cents=ann.per_winner_cents,
        remainder_cents=ann.remainder_cents,
        is_correction=ann.is_correction,
        currency=settings.tournament_currency,
        decimals=settings.tournament_currency_decimals,
    )


async def _post_winner(
    app_context: AppContext,
    context: ContextTypes.DEFAULT_TYPE,
    ann: TournamentWinnerAnnouncement,
) -> None:
    if not ann.is_correction:
        await _post_group(app_context, context, _winner_text(app_context, ann), what="o resultado")
        return

    # Correction: cap repeats so an oscillating re-grade can't spam the group.
    posts = app_context.tournament_corrections.get(ann.tournament_id, 0)
    if posts >= CORRECTION_POST_CAP:
        if posts == CORRECTION_POST_CAP:  # DM the admin exactly once, then stay silent
            app_context.tournament_corrections[ann.tournament_id] = posts + 1
            await notify_admin(
                context.bot,
                app_context.settings.admin_user_id,
                f"⚠️ Bolãozinho #{ann.tournament_id} recalculado de novo, mas o limite de "
                "correções no grupo foi atingido. Confira via /bolaozinho.",
            )
        return
    if await _post_group(app_context, context, _winner_text(app_context, ann), what="a correção"):
        app_context.tournament_corrections[ann.tournament_id] = posts + 1


async def post_tournament_announcements(
    app_context: AppContext,
    context: ContextTypes.DEFAULT_TYPE,
    announcements: Sequence[TournamentAnnouncement],
) -> None:
    """Post each result/no-result/correction announcement (best-effort, capped corrections)."""
    for ann in announcements:
        if isinstance(ann, TournamentWinnerAnnouncement):
            await _post_winner(app_context, context, ann)
        elif isinstance(ann, TournamentNoResultAnnouncement):
            await _post_group(
                app_context,
                context,
                tournament_no_result_text(name=ann.name),
                what="o encerramento do bolãozinho",
            )


async def resolve_and_post(
    app_context: AppContext, context: ContextTypes.DEFAULT_TYPE, fixture_id: int
) -> None:
    """Re-evaluate every bolãozinho containing ``fixture_id`` and post any announcements (§7).

    Called from every game state-change path (settle, void, un-void, sweep). Idempotent — when
    nothing changed, ``on_game_resolved`` returns no announcements and nothing is posted.
    """
    with app_context.session_factory() as session:
        announcements = on_game_resolved(session, fixture_id)
        session.commit()
    await post_tournament_announcements(app_context, context, announcements)
