"""PTB Application builder + startup validation (COMPLETION.md §4, §M4).

Grounding (per §2), verified June 2026:
- python-telegram-bot **22.x** — https://docs.python-telegram-bot.org/en/stable/
  ``ApplicationBuilder().token(...).post_init(cb).build()``; ``post_init`` runs after
  ``initialize()`` and before polling (used for ``set_my_commands``); ``BotCommandScope*`` for
  command scoping; ``application.add_error_handler``. See the 21.x→22.x decision in COMPLETION.md.
"""

from __future__ import annotations

from telegram import (
    Bot,
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler

from tigrinho.bot.alerts import error_handler
from tigrinho.bot.bets_handlers import register_bet_handlers, start_handler
from tigrinho.bot.board_handlers import register_board_handlers
from tigrinho.bot.help_handlers import cmd_ajuda
from tigrinho.bot.palpite_handlers import register_palpite_handlers
from tigrinho.bot.palpite_job import schedule_palpite_job
from tigrinho.bot.poll_job import schedule_poll_job
from tigrinho.bot.reminder_job import schedule_reminder_job
from tigrinho.bot.runtime import APP_CONTEXT_KEY, AnyApplication, AppContext, get_app_context
from tigrinho.bot.sync_job import schedule_sync_job
from tigrinho.config import Settings
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.app")

# Commands offered in DM (full set) vs in the group (read-only views); betting is DM-only (§8.2).
PRIVATE_COMMANDS: list[BotCommand] = [
    BotCommand("apostar", "Fazer um palpite"),
    BotCommand("minhas_apostas", "Ver e editar meus palpites"),
    BotCommand("jogos", "Próximos jogos e o que falta palpitar"),
    BotCommand("placar", "Ver o placar (Geral / Semana)"),
    BotCommand("placar_jogo", "Placar de um jogo já encerrado"),
    BotCommand("placar_jogos", "Placar somando vários jogos encerrados"),
    BotCommand("palpite", "Palpites da IA para os jogos de hoje"),
    BotCommand("ajuda", "Como funciona o bolão"),
]
GROUP_COMMANDS: list[BotCommand] = [
    BotCommand("jogos", "Próximos jogos"),
    BotCommand("placar", "Ver o placar (Geral / Semana)"),
    BotCommand("placar_jogo", "Placar de um jogo já encerrado"),
    BotCommand("placar_jogos", "Placar somando vários jogos encerrados"),
    BotCommand("palpite", "Palpites da IA para os jogos de hoje"),
    BotCommand("ajuda", "Como funciona o bolão"),
]


class StartupError(RuntimeError):
    """Raised when startup configuration validation fails (fail-fast, §4)."""


async def validate_startup(bot: Bot, settings: Settings) -> None:
    """Verify the live bot matches config: username + group reachability (fail-fast, §4)."""
    me = await bot.get_me()
    if me.username != settings.bot_username:
        raise StartupError(
            f"bot_username mismatch: config={settings.bot_username!r} "
            f"but get_me() says @{me.username}"
        )
    try:
        await bot.get_chat(settings.group_chat_id)
    except TelegramError as exc:
        raise StartupError(f"cannot reach group_chat_id={settings.group_chat_id}: {exc}") from exc
    # Admin DM reachability is best-effort (the admin must press Start once, §14).
    try:
        await bot.get_chat(settings.admin_user_id)
    except TelegramError as exc:
        _log.warning(
            "admin_dm_unreachable",
            admin_user_id=settings.admin_user_id,
            error=str(exc),
            hint="admin must press Start in the bot's private chat",
        )
    _log.info("startup_validated", bot_username=me.username, group_chat_id=settings.group_chat_id)


async def set_commands(bot: Bot) -> None:
    """Register slash commands with the right scopes (§8.2)."""
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())


async def post_init(application: AnyApplication) -> None:
    """Run startup validation, register commands, and schedule jobs before polling begins."""
    app_context = get_app_context(application)
    await validate_startup(application.bot, app_context.settings)
    await set_commands(application.bot)
    if application.job_queue is not None:
        schedule_sync_job(application.job_queue, app_context.settings)
        schedule_poll_job(application.job_queue, app_context.settings)
        schedule_reminder_job(application.job_queue, app_context.settings)
        schedule_palpite_job(application.job_queue, app_context.settings)


def build_application(app_context: AppContext) -> AnyApplication:
    """Build the PTB Application, wire shared context, handlers, and the error backstop."""
    application: AnyApplication = (
        ApplicationBuilder()
        .token(app_context.settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )
    application.bot_data[APP_CONTEXT_KEY] = app_context
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("ajuda", cmd_ajuda))
    # Board callbacks (pattern ^bv:) MUST be registered before the wizard's catch-all
    # CallbackQueryHandler so the toggle is matched first.
    register_board_handlers(application)
    register_palpite_handlers(application)
    register_bet_handlers(application)
    application.add_error_handler(error_handler)
    return application
