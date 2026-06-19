"""Execute a Splitwise registration: push the expense + persist the result (Feature 8 / §23).

Bridges the pure decision (:func:`tigrinho.splitwise_service.build_registration`) to the async
client. Network calls happen *outside* any open DB session (mirroring ``resolve_and_post``). All
entry points are best-effort: API/network failures are logged + DM'd to the admin and never crash
the caller. AUTO bolãozinhos register at settle; MANUAL ones go through the admin path.
"""

from __future__ import annotations

import httpx
from telegram.ext import ContextTypes

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.runtime import AppContext
from tigrinho.db.models import SplitwiseMode
from tigrinho.db.repositories import TournamentRepository
from tigrinho.domain.text_pt import escape
from tigrinho.logging import get_logger
from tigrinho.providers.splitwise import SplitwiseClient, SplitwiseError
from tigrinho.splitwise_service import SplitwiseRegistration, build_registration, mark_synced

_log = get_logger("tigrinho.splitwise_register")

# Most automatic Splitwise corrections per bolãozinho; beyond it the update is skipped + DMs admin.
CORRECTION_CAP = 2


async def _push(
    client: SplitwiseClient, *, group_id: int, currency_code: str, reg: SplitwiseRegistration
) -> int:
    """Create (or update, when an expense already exists) and return the expense id."""
    if reg.expense_id is None:
        return await client.create_expense(
            group_id=group_id,
            cost_cents=reg.cost_cents,
            currency_code=currency_code,
            description=reg.description,
            shares=list(reg.shares),
        )
    await client.update_expense(
        reg.expense_id,
        group_id=group_id,
        cost_cents=reg.cost_cents,
        currency_code=currency_code,
        description=reg.description,
        shares=list(reg.shares),
    )
    return reg.expense_id


async def register_tournament(app_context: AppContext, tournament_id: int) -> bool:
    """Build + push + persist one bolãozinho's expense. True if created/updated, False if nothing.

    Raises :class:`SplitwiseError` / ``httpx.HTTPError`` on API failure (callers handle it).
    The DB session is never held across the network call.
    """
    client = app_context.splitwise_client
    group_id = app_context.settings.splitwise_group_id
    if client is None or group_id is None:
        return False
    with app_context.session_factory() as session:
        reg = build_registration(session, tournament_id)
    if reg is None:
        return False
    expense_id = await _push(
        client,
        group_id=group_id,
        currency_code=app_context.settings.splitwise_currency_code,
        reg=reg,
    )
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is not None:
            mark_synced(tournament, expense_id=expense_id, signature=reg.signature)
            session.commit()
    return True


async def register_finished_tournament(
    app_context: AppContext,
    context: ContextTypes.DEFAULT_TYPE,
    tournament_id: int,
    *,
    is_correction: bool,
) -> None:
    """Auto-register an AUTO bolãozinho at settle (best-effort, capped corrections). §23."""
    if not app_context.settings.splitwise_enabled or app_context.splitwise_client is None:
        return
    with app_context.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        mode = tournament.splitwise_mode if tournament is not None else None
    if mode is not SplitwiseMode.AUTO:
        return  # MANUAL is admin-triggered; EXCLUDED is never touched.

    if is_correction:
        posts = app_context.splitwise_corrections.get(tournament_id, 0)
        if posts >= CORRECTION_CAP:
            if posts == CORRECTION_CAP:  # DM the admin exactly once, then stay silent
                app_context.splitwise_corrections[tournament_id] = posts + 1
                await notify_admin(
                    context.bot,
                    app_context.settings.admin_user_id,
                    f"⚠️ Bolãozinho #{tournament_id} recalculado de novo, mas o limite de "
                    "correções no Splitwise foi atingido. Ajuste manualmente se precisar.",
                )
            return

    try:
        changed = await register_tournament(app_context, tournament_id)
    except (SplitwiseError, httpx.HTTPError) as exc:
        _log.error("splitwise_register_failed", tournament_id=tournament_id, error=str(exc))
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Falha ao registrar o bolãozinho #{tournament_id} no Splitwise: "
            f"<code>{escape(str(exc))}</code>",
        )
        return
    if changed and is_correction:
        app_context.splitwise_corrections[tournament_id] = (
            app_context.splitwise_corrections.get(tournament_id, 0) + 1
        )
