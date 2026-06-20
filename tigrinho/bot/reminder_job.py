"""Pre-game betting reminders (COMPLETION.md §9.3).

A ``JobQueue.run_repeating`` job. Each sweep posts ONE group reminder for the soonest
unreminded kickoff slot due within ``reminder_lead_minutes`` of now — combining games that
share that exact kickoff time. Pure DB read + group post (no provider calls,
budget-independent). One bad cycle never kills the bot (§14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from telegram import InlineKeyboardMarkup, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes, JobQueue

from tigrinho.bot.alerts import notify_admin
from tigrinho.bot.keyboards import announcement_keyboard
from tigrinho.bot.runtime import AppContext, get_app_context
from tigrinho.config import Settings
from tigrinho.db.models import Game, utcnow
from tigrinho.db.repositories import BetRepository, GameRepository, TournamentRepository
from tigrinho.domain.bets import offerable_for
from tigrinho.domain.text_pt import escape, mention, reminder_text
from tigrinho.logging import get_logger

_log = get_logger("tigrinho.reminder_job")

REMINDER_JOB_NAME = "pre_game_reminder"
# Substrings marking a permanent (un-retryable) send failure — typically an oversized message or
# too many mention entities. We mark the slot reminded anyway so it never retry-spams (§22/F17).
_PERMANENT_SEND_ERRORS = ("too long", "too many", "entit", "can't parse")


@dataclass(frozen=True, slots=True)
class _GameView:
    """Plain snapshot of a game for message building (decoupled from the session)."""

    fixture_id: int
    home_team_name: str
    away_team_name: str
    kickoff_local: datetime
    bettors: tuple[tuple[str, int], ...]
    total_categories: int


def _bettors_for_game(bets: BetRepository, fixture_id: int) -> tuple[tuple[str, int], ...]:
    """Players who bet on this game and how many categories each filled (§9.3).

    Ordered most-complete first (count desc, then name) so the keenest bettors lead the list.
    """
    counts: dict[int, tuple[str, int]] = {}
    for bet in bets.list_for_game(fixture_id):
        name, placed = counts.get(bet.player_telegram_id, (bet.player.display_name, 0))
        counts[bet.player_telegram_id] = (name, placed + 1)
    ordered = sorted(counts.values(), key=lambda nc: (-nc[1], nc[0]))
    return tuple(ordered)


def _view(game: Game, bettors: tuple[tuple[str, int], ...]) -> _GameView:
    return _GameView(
        fixture_id=game.fixture_id,
        home_team_name=game.home_team_name,
        away_team_name=game.away_team_name,
        kickoff_local=game.kickoff_local,
        bettors=bettors,
        total_categories=len(offerable_for(game.category_set)),
    )


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reminder sweep callback (§9.3). One bad cycle must not kill the bot (§14)."""
    app_context = get_app_context(context.application)
    try:
        await _run_reminder(app_context, context)
    except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the bot (§14)
        _log.error("reminder_failed", error=str(exc), error_type=type(exc).__name__)
        await notify_admin(
            context.bot,
            app_context.settings.admin_user_id,
            f"⚠️ Lembrete falhou: <code>{escape(str(exc))}</code>",
        )


def _tournament_block(non_betting: list[tuple[int, str]], max_mentions: int) -> str:
    """The per-game 🏆 reminder line; mentions are deduped (by query) and capped to ``+N`` (F17)."""
    base = "🏆 Vale pelo bolãozinho!"
    if not non_betting:
        return base
    shown = non_betting[:max_mentions]
    extra = len(non_betting) - len(shown)
    names = ", ".join(mention(telegram_id, name) for telegram_id, name in shown)
    suffix = f" +{extra}" if extra > 0 else ""
    return f"{base} Ainda sem palpite: {names}{suffix} — corre!"


async def _send_reminder(
    app_context: AppContext,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    keyboard: InlineKeyboardMarkup,
    count: int,
) -> bool:
    """Send the reminder. Return True to mark the slot reminded (sent OR permanently skipped),
    False to leave it for a retry next sweep (transient failure)."""
    settings = app_context.settings
    try:
        await context.bot.send_message(
            chat_id=settings.group_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return True
    except BadRequest as exc:
        if any(marker in str(exc).lower() for marker in _PERMANENT_SEND_ERRORS):
            # Oversized / too many entities: marking it reminded avoids retry-spamming the same
            # un-sendable message every sweep (F17). The admin is told once.
            _log.error("reminder_skipped_permanent", error=str(exc), count=count)
            await notify_admin(
                context.bot,
                settings.admin_user_id,
                f"⚠️ Lembrete de {count} jogo(s) pulado (mensagem grande demais): "
                f"<code>{escape(str(exc))}</code>",
            )
            return True
        _log.error("reminder_send_failed", error=str(exc), count=count)
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Falha ao enviar lembrete de {count} jogo(s) (será reenviado): "
            f"<code>{escape(str(exc))}</code>",
        )
        return False
    except TelegramError as exc:
        _log.error("reminder_send_failed", error=str(exc), count=count)
        await notify_admin(
            context.bot,
            settings.admin_user_id,
            f"⚠️ Falha ao enviar lembrete de {count} jogo(s) (será reenviado): "
            f"<code>{escape(str(exc))}</code>",
        )
        return False


async def _run_reminder(app_context: AppContext, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = app_context.settings
    now = utcnow()
    with app_context.session_factory() as session:
        tournaments = TournamentRepository(session)
        open_ids = tournaments.open_member_fixture_ids()
        games = GameRepository(session).list_due_for_reminder(
            now, settings.reminder_lead, extra_eligible_ids=open_ids
        )
        bets = BetRepository(session)
        views = [_view(g, _bettors_for_game(bets, g.fixture_id)) for g in games]
        blocks = [
            _tournament_block(
                tournaments.non_betting_entrants_for_game(g.fixture_id),
                settings.reminder_max_mentions,
            )
            if g.fixture_id in open_ids
            else ""
            for g in games
        ]
    if not views:
        return

    text = reminder_text(
        [
            (v.home_team_name, v.away_team_name, v.kickoff_local, v.bettors, v.total_categories)
            for v in views
        ],
        tournament_blocks=blocks,
    )
    keyboard = announcement_keyboard(
        [(v.fixture_id, f"{v.home_team_name} x {v.away_team_name}") for v in views],
        settings.bot_username,
    )
    if not await _send_reminder(
        app_context, context, text=text, keyboard=keyboard, count=len(views)
    ):
        return  # transient failure — leave unmarked so the next sweep retries

    with app_context.session_factory() as session:
        GameRepository(session).mark_reminded([v.fixture_id for v in views], now)
        session.commit()
    _log.info("reminded", count=len(views))


def schedule_reminder_job(
    job_queue: JobQueue[ContextTypes.DEFAULT_TYPE], settings: Settings
) -> None:
    """Schedule the reminder sweep every ``reminder_interval_minutes`` (§9.3)."""
    job_queue.run_repeating(
        reminder_job,
        interval=settings.reminder_interval_minutes * 60,
        first=20,
        name=REMINDER_JOB_NAME,
    )
