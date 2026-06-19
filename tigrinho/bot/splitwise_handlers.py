"""Splitwise linking + admin manual-register handlers (Feature 8 / §23).

Keyboard-first: ``/vincular_splitwise`` (DM) asks "já está no grupo?" — **Sim** → pick yourself from
the group roster (matched by Splitwise ``user_id``, never duplicated under a mistyped email);
**Não** → type an email and we invite you. The single free-text step (the email) is captured via a
``context.user_data`` flag + a guarded ``MessageHandler`` (the only stateful wizard step).
"""

from __future__ import annotations

import re

import httpx
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tigrinho.bot.callbacks import (
    SplitwiseInGroup,
    SplitwiseMemberPick,
    SplitwiseNotInGroup,
    SplitwiseRegisterPick,
    decode,
)
from tigrinho.bot.keyboards import (
    splitwise_intro_keyboard,
    splitwise_member_keyboard,
    splitwise_register_keyboard,
)
from tigrinho.bot.messaging import safe_edit_text
from tigrinho.bot.runtime import AnyApplication, AppContext, get_app_context
from tigrinho.bot.splitwise_register import register_tournament
from tigrinho.db.repositories import PlayerRepository
from tigrinho.domain import text_pt
from tigrinho.logging import get_logger
from tigrinho.providers.splitwise import SplitwiseError, SplitwiseMember
from tigrinho.splitwise_service import manual_registerable

_log = get_logger("tigrinho.splitwise_handlers")
_AWAIT_EMAIL_KEY = "awaiting_splitwise_email"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _display_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "Tigrinho"
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


# --- /vincular_splitwise entry --------------------------------------------------------------------
async def start_link_wizard_dm(update: Update, app_context: AppContext) -> None:
    """Open the link wizard in DM (also the ``?start=vincular`` deep-link target). §23."""
    message = update.effective_message
    if message is None:
        return
    if not app_context.settings.splitwise_enabled:
        await message.reply_text(text_pt.splitwise_not_configured_text())
        return
    await message.reply_text(
        text_pt.splitwise_link_intro_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=splitwise_intro_keyboard(),
    )


async def cmd_vincular_splitwise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vincular_splitwise — DM opens the wizard; in a group, redirect to the private chat. §23."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    app_context = get_app_context(context.application)
    if not app_context.settings.splitwise_enabled:
        await message.reply_text(text_pt.splitwise_not_configured_text())
        return
    if chat.type != ChatType.PRIVATE:
        url = f"https://t.me/{app_context.settings.bot_username}?start=vincular"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👉 Vincular no privado", url=url)]])
        await message.reply_text("Vincule o Splitwise no meu privado 👇", reply_markup=keyboard)
        return
    await start_link_wizard_dm(update, app_context)


# --- callbacks ------------------------------------------------------------------------------------
async def on_splitwise_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatcher for the link wizard + admin register buttons (sv/sn/sp/sr). §23."""
    query = update.callback_query
    user = update.effective_user
    if query is None or query.data is None or user is None:
        return
    try:
        data = decode(query.data)
    except ValueError:
        await query.answer("Ação inválida ou expirada.")
        return
    app_context = get_app_context(context.application)
    match data:
        case SplitwiseInGroup():
            await _show_member_picker(query, app_context)
        case SplitwiseNotInGroup():
            await _ask_email(query, context)
        case SplitwiseMemberPick(splitwise_user_id):
            await _link_member(query, app_context, update, splitwise_user_id)
        case SplitwiseRegisterPick(tournament_id):
            await _admin_register(query, app_context, user.id, tournament_id)
        case _:  # pragma: no cover - pattern guarantees a splitwise op
            await query.answer()


async def _fetch_members(app_context: AppContext) -> list[SplitwiseMember] | None:
    """Group roster, or None on a disabled/erroring client (caller shows a friendly message)."""
    client = app_context.splitwise_client
    group_id = app_context.settings.splitwise_group_id
    if client is None or group_id is None:
        return None
    try:
        return await client.get_group_members(group_id)
    except (SplitwiseError, httpx.HTTPError) as exc:
        _log.error("splitwise_get_group_failed", error=str(exc))
        return None


def _linked_user_ids(app_context: AppContext) -> set[int]:
    with app_context.session_factory() as session:
        return {
            p.splitwise_user_id
            for p in PlayerRepository(session).list_all()
            if p.splitwise_user_id is not None
        }


async def _show_member_picker(query: CallbackQuery, app_context: AppContext) -> None:
    await query.answer()
    members = await _fetch_members(app_context)
    if members is None:
        await safe_edit_text(query, "Não consegui falar com o Splitwise agora. Tenta de novo? 🐯")
        return
    linked = _linked_user_ids(app_context)
    unlinked = [(m.id, m.display_name) for m in members if m.id not in linked]
    text = "Quem é você no grupo do Splitwise?" if unlinked else text_pt.splitwise_all_linked_text()
    await safe_edit_text(query, text, reply_markup=splitwise_member_keyboard(unlinked))


async def _ask_email(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        context.user_data[_AWAIT_EMAIL_KEY] = True
    await query.answer()
    await safe_edit_text(query, text_pt.splitwise_ask_email_text())


async def _link_member(
    query: CallbackQuery, app_context: AppContext, update: Update, splitwise_user_id: int
) -> None:
    user = update.effective_user
    if user is None:
        return
    members = await _fetch_members(app_context)
    member = next((m for m in (members or []) if m.id == splitwise_user_id), None)
    if member is None:
        await query.answer("Esse membro não está mais disponível.", show_alert=True)
        return
    with app_context.session_factory() as session:
        player = PlayerRepository(session).get_or_create(user.id, _display_name(update))
        player.splitwise_user_id = member.id
        player.splitwise_email = member.email
        session.commit()
    await query.answer()
    await safe_edit_text(query, text_pt.splitwise_linked_text(member_name=member.display_name))


async def on_splitwise_email_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture the typed email for the 'Não estou no grupo' branch (guarded by user_data). §23."""
    if not (context.user_data and context.user_data.get(_AWAIT_EMAIL_KEY)):
        return
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return
    email = message.text.strip()
    if not _EMAIL_RE.match(email):
        await message.reply_text(text_pt.splitwise_invalid_email_text())
        return
    context.user_data[_AWAIT_EMAIL_KEY] = False
    app_context = get_app_context(context.application)
    client = app_context.splitwise_client
    group_id = app_context.settings.splitwise_group_id
    if client is None or group_id is None:
        await message.reply_text(text_pt.splitwise_not_configured_text())
        return
    name = _display_name(update)
    try:
        sw_user = await client.add_user_to_group(group_id, email=email, first_name=name)
    except (SplitwiseError, httpx.HTTPError) as exc:
        _log.error("splitwise_add_user_failed", error=str(exc))
        await message.reply_text(
            "Não consegui te adicionar ao grupo do Splitwise agora. "
            "Tenta /vincular_splitwise de novo? 🐯"
        )
        return
    with app_context.session_factory() as session:
        player = PlayerRepository(session).get_or_create(user.id, name)
        player.splitwise_user_id = sw_user.id
        player.splitwise_email = email
        session.commit()
    await message.reply_text(
        text_pt.splitwise_linked_text(member_name=name), parse_mode=ParseMode.HTML
    )


# --- admin manual register ------------------------------------------------------------------------
async def cmd_bolaozinho_splitwise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bolaozinho_splitwise (admin) — picker of bolãozinhos ready for manual registration. §23."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    app_context = get_app_context(context.application)
    if user.id != app_context.settings.admin_user_id:
        await message.reply_text("Só o admin pode registrar bolãozinhos no Splitwise.")
        return
    if not app_context.settings.splitwise_enabled:
        await message.reply_text(text_pt.splitwise_not_configured_text())
        return
    with app_context.session_factory() as session:
        ready = manual_registerable(session)
        items = [(t.id, f"#{t.id} {t.name}") for t in ready]
    if not items:
        await message.reply_text("Nenhum bolãozinho pronto pra registrar no Splitwise. 🐯")
        return
    await message.reply_text(
        "Qual bolãozinho registrar no Splitwise?",
        reply_markup=splitwise_register_keyboard(items),
    )


async def _admin_register(
    query: CallbackQuery,
    app_context: AppContext,
    actor_id: int,
    tournament_id: int,
) -> None:
    if actor_id != app_context.settings.admin_user_id:
        await query.answer("Só o admin pode fazer isso.", show_alert=True)
        return
    await query.answer()
    try:
        changed = await register_tournament(app_context, tournament_id)
    except (SplitwiseError, httpx.HTTPError) as exc:
        _log.error("splitwise_manual_register_failed", tournament_id=tournament_id, error=str(exc))
        await safe_edit_text(query, f"Falha ao registrar: {text_pt.escape(str(exc))}")
        return
    if changed:
        await safe_edit_text(query, f"✅ Bolãozinho #{tournament_id} registrado no Splitwise.")
    else:
        await safe_edit_text(query, "Nada a registrar (sem resultado ou já registrado).")


def register_splitwise_handlers(application: AnyApplication) -> None:
    """Register the linking wizard, admin register command, and the guarded email MessageHandler."""
    application.add_handler(CommandHandler("vincular_splitwise", cmd_vincular_splitwise))
    application.add_handler(CommandHandler("bolaozinho_splitwise", cmd_bolaozinho_splitwise))
    application.add_handler(CallbackQueryHandler(on_splitwise_callback, pattern="^(sv|sn|sp|sr)"))
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, on_splitwise_email_text
        )
    )
