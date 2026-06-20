"""Typer admin CLI (COMPLETION.md §13).

Run inside the container: ``docker compose exec bot python -m tigrinho.cli <command>``. Shares the
repositories + domain + settlement/scoreboard code with the bot. Destructive commands require an
explicit ``--yes`` flag. Output is plain aligned tables so it stays readable over ``compose exec``.

Capability groups (§13):
  1. CRUD games/bets/players  — ``games``/``bets``/``players`` sub-commands
  2. manual result & re-settle — ``set-result`` (idempotent)
  3. force sync & budget       — ``sync``, ``budget``
  4. recalc board & DB dump    — ``board``, ``db dump``
  plus ``telegram-info``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import typer
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from telegram import Bot, User

from tigrinho import tournament_service as svc
from tigrinho.board_data import load_board_records
from tigrinho.config import Settings, get_settings
from tigrinho.db.engine import create_db_engine, create_session_factory
from tigrinho.db.models import GameStatus, SplitwiseMode, TournamentStatus, utcnow
from tigrinho.db.repositories import (
    ApiUsageRepository,
    BetRepository,
    GameRepository,
    PlayerRepository,
    TournamentRepository,
)
from tigrinho.domain.bets import BetCategory
from tigrinho.domain.text_pt import format_money_cents
from tigrinho.domain.tournament import compute_outcome, parse_price_to_cents
from tigrinho.providers.base import FootballProvider, GoalEvent, MatchResult
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.factory import make_provider
from tigrinho.providers.splitwise import SplitwiseClient
from tigrinho.scoreboard import rank
from tigrinho.settlement_service import settle_fixture
from tigrinho.splitwise_service import (
    SplitwiseRegistration,
    build_forced_registration,
    build_registration,
    mark_synced,
)

_DUMPABLE_TABLES = ("players", "games", "bets", "api_usage")


@dataclass(frozen=True, slots=True)
class CliContext:
    settings: Settings
    session_factory: sessionmaker[Session]
    provider: FootballProvider
    budget: RequestBudget


def build_cli_context() -> CliContext:
    """Assemble the CLI's dependencies from the validated settings (monkeypatched in tests)."""
    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    session_factory = create_session_factory(engine)
    return CliContext(
        settings=settings,
        session_factory=session_factory,
        provider=make_provider(settings),
        budget=RequestBudget(
            session_factory, daily_cap=settings.api_daily_cap, reset_tz=settings.budget_tzinfo
        ),
    )


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    typer.echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    typer.echo("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        typer.echo("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


app = typer.Typer(no_args_is_help=True, help="TigrinhoDaCopa admin CLI")
games_app = typer.Typer(no_args_is_help=True, help="CRUD on games")
bets_app = typer.Typer(no_args_is_help=True, help="CRUD on bets")
players_app = typer.Typer(no_args_is_help=True, help="CRUD on players")
bolaozinho_app = typer.Typer(no_args_is_help=True, help="Manage bolãozinhos (tournaments)")
app.add_typer(games_app, name="games")
app.add_typer(bets_app, name="bets")
app.add_typer(players_app, name="players")
app.add_typer(bolaozinho_app, name="bolaozinho")


# --- group 1: CRUD --------------------------------------------------------------------------


@games_app.command("list")
def games_list() -> None:
    """List all games."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        rows = [
            [
                str(g.fixture_id),
                f"{g.home_team_name} x {g.away_team_name}",
                g.kickoff_utc.isoformat(),
                g.status.value,
            ]
            for g in GameRepository(session).list_all()
        ]
    _print_table(["fixture_id", "match", "kickoff_utc", "status"], rows)


@games_app.command("show")
def games_show(fixture_id: int) -> None:
    """Show one game and its bets."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None:
            typer.echo(f"Game {fixture_id} not found.")
            raise typer.Exit(code=1)
        typer.echo(
            f"#{game.fixture_id} {game.home_team_name} x {game.away_team_name} "
            f"[{game.stage.value}/{game.status.value}] kickoff={game.kickoff_utc.isoformat()} "
            f"score={game.home_goals_90}-{game.away_goals_90}"
        )
        rows = [
            [
                str(b.id),
                str(b.player_telegram_id),
                b.category,
                b.payload_json,
                str(b.points_awarded),
            ]
            for b in BetRepository(session).list_for_game(fixture_id)
        ]
    _print_table(["bet_id", "player", "category", "payload", "points"], rows)


@games_app.command("delete")
def games_delete(
    fixture_id: int, yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion")] = False
) -> None:
    """Delete a game (and its bets, via cascade)."""
    if not yes:
        typer.echo("Refusing to delete without --yes.")
        raise typer.Exit(code=1)
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        deleted = GameRepository(session).delete(fixture_id)
        session.commit()
    typer.echo("Deleted." if deleted else "Not found.")


@players_app.command("list")
def players_list() -> None:
    """List players."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        rows = [
            [str(p.telegram_id), p.display_name, p.created_at.isoformat()]
            for p in PlayerRepository(session).list_all()
        ]
    _print_table(["telegram_id", "name", "created_at"], rows)


@players_app.command("delete")
def players_delete(telegram_id: int, yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    """Delete a player (and their bets, via cascade)."""
    if not yes:
        typer.echo("Refusing to delete without --yes.")
        raise typer.Exit(code=1)
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        deleted = PlayerRepository(session).delete(telegram_id)
        session.commit()
    typer.echo("Deleted." if deleted else "Not found.")


@bets_app.command("list")
def bets_list(
    player: Annotated[int | None, typer.Option(help="Filter by player telegram id")] = None,
    game: Annotated[int | None, typer.Option(help="Filter by fixture id")] = None,
) -> None:
    """List bets, optionally filtered by player and/or game."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        repo = BetRepository(session)
        if player is not None and game is not None:
            bets = repo.list_for_player_and_game(player, game)
        elif player is not None:
            bets = repo.list_for_player(player)
        elif game is not None:
            bets = repo.list_for_game(game)
        else:
            bets = repo.list_all()
        rows = [
            [
                str(b.id),
                str(b.fixture_id),
                str(b.player_telegram_id),
                b.category,
                str(b.points_awarded),
            ]
            for b in bets
        ]
    _print_table(["bet_id", "fixture", "player", "category", "points"], rows)


@bets_app.command("delete")
def bets_delete(bet_id: int, yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    """Delete a bet."""
    if not yes:
        typer.echo("Refusing to delete without --yes.")
        raise typer.Exit(code=1)
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        deleted = BetRepository(session).delete(bet_id)
        session.commit()
    typer.echo("Deleted." if deleted else "Not found.")


# --- group 2: manual result & re-settle -----------------------------------------------------


@app.command("set-result")
def set_result(
    fixture_id: int,
    home: int,
    away: int,
    first_team: Annotated[
        str | None, typer.Option(help="First team to score: 'home' or 'away'")
    ] = None,
    advancing: Annotated[int | None, typer.Option(help="Advancing team id (knockout)")] = None,
    ht_home: Annotated[
        int | None, typer.Option(help="Half-time home goals (for HALF_TIME_RESULT)")
    ] = None,
    ht_away: Annotated[
        int | None, typer.Option(help="Half-time away goals (for HALF_TIME_RESULT)")
    ] = None,
) -> None:
    """Set/override a game's 90′ score (+ optional first team / advancing / HT) and re-settle.

    Pass ``--ht-home``/``--ht-away`` to grade HALF_TIME_RESULT bets; without them, any such bet on
    the game voids (scores 0) rather than crashing the re-settle.
    """
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None:
            typer.echo(f"Game {fixture_id} not found.")
            raise typer.Exit(code=1)
        goals: tuple[GoalEvent, ...] = ()
        if first_team is not None:
            choice = first_team.lower()
            if choice not in ("home", "away"):
                typer.echo("--first-team must be 'home' or 'away'.")
                raise typer.Exit(code=1)
            team_id = game.home_team_id if choice == "home" else game.away_team_id
            goals = (
                GoalEvent(
                    minute=1,
                    team_id=team_id,
                    player_id=None,
                    player_name=None,
                    is_own_goal=False,
                    is_penalty=False,
                ),
            )
        result = MatchResult(
            fixture_id=fixture_id,
            stage=game.stage,
            status=GameStatus.FINISHED,
            home_goals_90=home,
            away_goals_90=away,
            goals=goals,
            advancing_team_id=advancing,
            home_goals_ht=ht_home,
            away_goals_ht=ht_away,
        )
        summary = settle_fixture(session, game, result)
        has_ht_bets = any(
            b.category == BetCategory.HALF_TIME_RESULT.value
            for b in BetRepository(session).list_for_game(fixture_id)
        )
        session.commit()
    typer.echo(
        f"Settled #{fixture_id}: {summary.home_team_name} {home} x {away} "
        f"{summary.away_team_name}; {len(summary.players)} player(s) graded."
    )
    if has_ht_bets and (ht_home is None or ht_away is None):
        typer.echo(
            "Note: HALF_TIME_RESULT bets exist but no half-time score was given "
            "(--ht-home/--ht-away) — those bets voided (0 pts)."
        )


# --- group 3: force sync & cache ops --------------------------------------------------------


@app.command("sync")
def sync() -> None:
    """Force a fixtures sync now (DB only; no group announcement)."""
    from tigrinho.bot.sync_job import sync_fixtures

    ctx = build_cli_context()
    fixtures = asyncio.run(ctx.budget.guarded(lambda: ctx.provider.get_fixtures(48)))
    with ctx.session_factory() as session:
        outcome = sync_fixtures(session, fixtures, tz=ctx.settings.tzinfo)
        new, resched, voided = (
            len(outcome.new_games),
            len(outcome.rescheduled_games),
            len(outcome.voided_games),
        )
        session.commit()
    typer.echo(f"Sync done: new={new} rescheduled={resched} voided={voided}.")


@app.command("budget")
def budget() -> None:
    """Print today's API request counter and the remaining budget."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        used = ApiUsageRepository(session).get_count(ctx.budget.today())
    typer.echo(
        f"Budget {ctx.budget.today().isoformat()}: used {used}/{ctx.settings.api_daily_cap} "
        f"(remaining {max(0, ctx.settings.api_daily_cap - used)})."
    )


# --- group 4: recalc board & DB dump --------------------------------------------------------


@app.command("board")
def board(weekly: Annotated[bool, typer.Option("--weekly", help="Weekly board")] = False) -> None:
    """Rebuild and print the scoreboard from settled bets."""
    ctx = build_cli_context()
    now_local = datetime.now(ctx.settings.tzinfo).replace(tzinfo=None)
    with ctx.session_factory() as session:
        entries = rank(load_board_records(session, weekly=weekly, now_local=now_local))
    rows = [[str(e.rank), e.display_name, str(e.points), str(e.correct)] for e in entries]
    _print_table(["rank", "player", "points", "correct"], rows)


@app.command("db")
def db_dump(
    table: Annotated[str | None, typer.Option(help="Single table to dump")] = None,
) -> None:
    """Dump table rows as JSON for debugging."""
    if table is not None and table not in _DUMPABLE_TABLES:
        typer.echo(f"Unknown table {table!r}. Known: {', '.join(_DUMPABLE_TABLES)}")
        raise typer.Exit(code=1)
    tables = (table,) if table is not None else _DUMPABLE_TABLES
    ctx = build_cli_context()
    dump: dict[str, list[dict[str, object]]] = {}
    with ctx.session_factory() as session:
        for name in tables:
            result = session.execute(text(f"SELECT * FROM {name}"))  # noqa: S608 - name allowlisted
            dump[name] = [dict(row) for row in result.mappings()]
    typer.echo(json.dumps(dump, indent=2, default=str))


# --- telegram-info --------------------------------------------------------------------------


async def _get_me(settings: Settings) -> User:
    bot = Bot(settings.telegram_bot_token)
    async with bot:
        return await bot.get_me()


@app.command("telegram-info")
def telegram_info() -> None:
    """Print the bot's resolved @username/id and the configured group/admin ids."""
    ctx = build_cli_context()
    me = asyncio.run(_get_me(ctx.settings))
    typer.echo(f"bot: @{me.username} (id={me.id})")
    typer.echo(f"configured bot_username: {ctx.settings.bot_username}")
    typer.echo(f"group_chat_id: {ctx.settings.group_chat_id}")
    typer.echo(f"admin_user_id: {ctx.settings.admin_user_id}")


# --- group 5: bolãozinhos (tournaments, §22) ------------------------------------------------


def _money(ctx: CliContext, cents: int) -> str:
    return format_money_cents(
        cents,
        currency=ctx.settings.tournament_currency,
        decimals=ctx.settings.tournament_currency_decimals,
    )


@bolaozinho_app.command("create")
def bolaozinho_create(
    name: str,
    price: Annotated[str, typer.Option("--price", help="Entry price, e.g. 10 or 10,50")],
) -> None:
    """Create a DRAFT bolãozinho owned by the configured admin."""
    ctx = build_cli_context()
    try:
        price_cents = parse_price_to_cents(price)
    except ValueError as exc:
        typer.echo(f"Invalid price: {exc}")
        raise typer.Exit(code=1) from exc
    with ctx.session_factory() as session:
        tournament = svc.create_tournament(
            session, name=name, entry_price_cents=price_cents, created_by=ctx.settings.admin_user_id
        )
        session.commit()
        typer.echo(f"Created bolãozinho #{tournament.id}: {name} ({_money(ctx, price_cents)})")


@bolaozinho_app.command("list")
def bolaozinho_list() -> None:
    """List all bolãozinhos."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        repo = TournamentRepository(session)
        rows = [
            [
                str(t.id),
                t.name,
                t.status.value,
                _money(ctx, t.entry_price_cents),
                str(repo.count_entries(t.id)),
                _money(ctx, repo.count_entries(t.id) * t.entry_price_cents),
            ]
            for t in repo.list_all()
        ]
    _print_table(["id", "name", "status", "price", "entrants", "pot"], rows)


@bolaozinho_app.command("show")
def bolaozinho_show(tournament_id: int) -> None:
    """Show one bolãozinho: games, entrants, and the current standings."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        repo = TournamentRepository(session)
        tournament = repo.get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        typer.echo(
            f"#{tournament.id} {tournament.name} [{tournament.status.value}] "
            f"entrada={_money(ctx, tournament.entry_price_cents)}"
        )
        typer.echo("Games:")
        for game in repo.list_games(tournament.id):
            typer.echo(
                f"  #{game.fixture_id} {game.home_team_name} x {game.away_team_name} "
                f"[{game.status.value}]"
            )
        standings = repo.standings(tournament.id)
        players = PlayerRepository(session)
        typer.echo("Standings:")
        for telegram_id, points in sorted(standings.items(), key=lambda kv: (-kv[1], kv[0])):
            player = players.get(telegram_id)
            name = player.display_name if player is not None else str(telegram_id)
            typer.echo(f"  {name}: {points}")


@bolaozinho_app.command("add-game")
def bolaozinho_add_game(tournament_id: int, fixture_id: int) -> None:
    """Add a (scheduled, future) game to a bolãozinho."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        try:
            svc.add_game(session, tournament, fixture_id, now=utcnow())
        except svc.TournamentError as exc:
            typer.echo(exc.message)
            raise typer.Exit(code=1) from exc
        session.commit()
    typer.echo("Added.")


@bolaozinho_app.command("remove-game")
def bolaozinho_remove_game(tournament_id: int, fixture_id: int) -> None:
    """Remove a game from a bolãozinho (before it locks)."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        try:
            svc.remove_game(session, tournament, fixture_id, now=utcnow())
        except svc.TournamentError as exc:
            typer.echo(exc.message)
            raise typer.Exit(code=1) from exc
        session.commit()
    typer.echo("Removed.")


@bolaozinho_app.command("set-price")
def bolaozinho_set_price(tournament_id: int, price: str) -> None:
    """Set the entry price (before the first entry / lock)."""
    ctx = build_cli_context()
    try:
        price_cents = parse_price_to_cents(price)
    except ValueError as exc:
        typer.echo(f"Invalid price: {exc}")
        raise typer.Exit(code=1) from exc
    with ctx.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        try:
            svc.set_price(session, tournament, price_cents, now=utcnow())
        except svc.TournamentError as exc:
            typer.echo(exc.message)
            raise typer.Exit(code=1) from exc
        session.commit()
    typer.echo(f"Price set to {_money(ctx, price_cents)}.")


@bolaozinho_app.command("cancel")
def bolaozinho_cancel(
    tournament_id: int,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    reason: Annotated[str | None, typer.Option("--reason", help="Why it was cancelled")] = None,
) -> None:
    """Cancel a bolãozinho (CLI does not DM participants — use the bot command for that)."""
    if not yes:
        typer.echo("Refusing to cancel without --yes.")
        raise typer.Exit(code=1)
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        try:
            svc.cancel_tournament(session, tournament, reason=reason)
        except svc.TournamentError as exc:
            typer.echo(exc.message)
            raise typer.Exit(code=1) from exc
        session.commit()
    typer.echo("Cancelled.")


@bolaozinho_app.command("entries")
def bolaozinho_entries(tournament_id: int) -> None:
    """List a bolãozinho's entrants."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        repo = TournamentRepository(session)
        players = PlayerRepository(session)
        rows = []
        for telegram_id in repo.entry_ids(tournament_id):
            player = players.get(telegram_id)
            rows.append([str(telegram_id), player.display_name if player is not None else "?"])
    _print_table(["telegram_id", "name"], rows)


@bolaozinho_app.command("add-entry")
def bolaozinho_add_entry(tournament_id: int, telegram_id: int) -> None:
    """Admin fixup: add a player to a bolãozinho (creates the player row if needed)."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        players = PlayerRepository(session)
        # Only seed a placeholder name for a brand-new player; never clobber an
        # existing player's real display name (get_or_create would overwrite it).
        if players.get(telegram_id) is None:
            players.get_or_create(telegram_id, str(telegram_id))
        added = TournamentRepository(session).add_entry(tournament_id, telegram_id)
        session.commit()
    typer.echo("Added." if added else "Already entered.")


@bolaozinho_app.command("remove-entry")
def bolaozinho_remove_entry(tournament_id: int, telegram_id: int) -> None:
    """Admin fixup: remove a player from a bolãozinho."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        removed = TournamentRepository(session).remove_entry(tournament_id, telegram_id)
        session.commit()
    typer.echo("Removed." if removed else "Not entered.")


@bolaozinho_app.command("standings")
def bolaozinho_standings(tournament_id: int) -> None:
    """Print the recomputed standings + payout outcome (read-only, from settled bets)."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        repo = TournamentRepository(session)
        tournament = repo.get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        outcome = compute_outcome(repo.standings(tournament.id), tournament.entry_price_cents)
        players = PlayerRepository(session)
        winner_names = [
            (player.display_name if (player := players.get(tid)) is not None else str(tid))
            for tid in outcome.winner_ids
        ]
    typer.echo(f"pot={_money(ctx, outcome.pot_cents)} prize={_money(ctx, outcome.prize_cents)}")
    typer.echo(f"winners={winner_names} score={outcome.winning_score}")
    typer.echo(
        f"per_winner={_money(ctx, outcome.per_winner_cents)} "
        f"remainder={_money(ctx, outcome.remainder_cents)}"
    )


# --- Splitwise (Feature 8 / §23) -----------------------------------------------------------------
async def _push_to_splitwise(settings: Settings, reg: SplitwiseRegistration) -> int:
    """Create/update the expense on Splitwise and return its id (CLI builds its own client)."""
    assert settings.splitwise_api_key is not None and settings.splitwise_group_id is not None
    client = SplitwiseClient(
        base_url=settings.splitwise_base_url, api_key=settings.splitwise_api_key
    )
    try:
        if reg.expense_id is None:
            return await client.create_expense(
                group_id=settings.splitwise_group_id,
                cost_cents=reg.cost_cents,
                currency_code=settings.splitwise_currency_code,
                description=reg.description,
                shares=list(reg.shares),
            )
        await client.update_expense(
            reg.expense_id,
            group_id=settings.splitwise_group_id,
            cost_cents=reg.cost_cents,
            currency_code=settings.splitwise_currency_code,
            description=reg.description,
            shares=list(reg.shares),
        )
        return reg.expense_id
    finally:
        await client.aclose()


@bolaozinho_app.command("splitwise-status")
def bolaozinho_splitwise_status(
    tournament_id: Annotated[int | None, typer.Argument()] = None,
) -> None:
    """Show each bolãozinho's Splitwise mode, expense id, and linked/total entrants (read-only)."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        repo = TournamentRepository(session)
        players = PlayerRepository(session)
        if tournament_id is None:
            tournaments = repo.list_all()
        else:
            one = repo.get(tournament_id)
            tournaments = [one] if one is not None else []
        rows = []
        for tournament in tournaments:
            ids = repo.entry_ids(tournament.id)
            linked = sum(
                1
                for tid in ids
                if (p := players.get(tid)) is not None and p.splitwise_user_id is not None
            )
            rows.append(
                [
                    str(tournament.id),
                    tournament.name,
                    tournament.splitwise_mode.value,
                    str(tournament.splitwise_expense_id or "-"),
                    f"{linked}/{len(ids)}",
                ]
            )
    _print_table(["id", "name", "mode", "expense", "linked"], rows)


@bolaozinho_app.command("splitwise-exclude")
def bolaozinho_splitwise_exclude(
    tournament_id: int, yes: Annotated[bool, typer.Option("--yes")] = False
) -> None:
    """Mark a bolãozinho EXCLUDED so the bot never registers it (e.g. already settled by hand)."""
    if not yes:
        typer.echo("Refusing to change mode without --yes.")
        raise typer.Exit(code=1)
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is None:
            typer.echo("Not found.")
            raise typer.Exit(code=1)
        tournament.splitwise_mode = SplitwiseMode.EXCLUDED
        session.commit()
    typer.echo("Excluded.")


@bolaozinho_app.command("register-splitwise")
def bolaozinho_register_splitwise(
    tournament_id: int,
    force: Annotated[bool, typer.Option("--force", help="Register among LINKED entrants only")] = (
        False
    ),
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Manually register a finished bolãozinho's result in Splitwise (admin escape hatch)."""
    ctx = build_cli_context()
    if not ctx.settings.splitwise_enabled:
        typer.echo("Splitwise is not configured.")
        raise typer.Exit(code=1)
    if force and not yes:
        typer.echo("--force drops unlinked losers; pass --yes to confirm.")
        raise typer.Exit(code=1)
    with ctx.session_factory() as session:
        try:
            reg = (
                build_forced_registration(session, tournament_id)
                if force
                else build_registration(session, tournament_id)
            )
        except ValueError as exc:
            typer.echo(f"Cannot register: {exc}")
            raise typer.Exit(code=1) from exc
        if reg is None:
            typer.echo("Nothing to register (no result, unlinked entrants, or already synced).")
            raise typer.Exit(code=1)
    expense_id = asyncio.run(_push_to_splitwise(ctx.settings, reg))
    with ctx.session_factory() as session:
        tournament = TournamentRepository(session).get(tournament_id)
        if tournament is not None:
            mark_synced(tournament, expense_id=expense_id, signature=reg.signature)
            session.commit()
    typer.echo(f"Registered expense {expense_id} (cost={_money(ctx, reg.cost_cents)}).")


async def _dm_nudge(settings: Settings, targets: list[tuple[int, str]]) -> int:
    """Best-effort DM each (telegram_id, name) the link prompt; return how many were reached."""
    bot = Bot(settings.telegram_bot_token)
    reached = 0
    async with bot:
        for telegram_id, _name in targets:
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text="🔗 Vincule seu Splitwise pra eu acertar o bolãozinho: "
                    "/vincular_splitwise",
                )
                reached += 1
            except Exception:  # noqa: BLE001 - best-effort; one unreachable user mustn't stop the rest
                continue
    return reached


@bolaozinho_app.command("nudge-splitwise")
def bolaozinho_nudge_splitwise(yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    """DM unlinked entrants of OPEN, non-excluded bolãozinhos to link Splitwise (best-effort)."""
    if not yes:
        typer.echo("This DMs people; pass --yes to confirm.")
        raise typer.Exit(code=1)
    ctx = build_cli_context()
    targets: dict[int, str] = {}
    with ctx.session_factory() as session:
        repo = TournamentRepository(session)
        players = PlayerRepository(session)
        for tournament in repo.list_by_status(TournamentStatus.OPEN):
            if tournament.splitwise_mode is SplitwiseMode.EXCLUDED:
                continue
            for tid in repo.entry_ids(tournament.id):
                player = players.get(tid)
                if player is not None and player.splitwise_user_id is None:
                    targets[tid] = player.display_name
    reached = asyncio.run(_dm_nudge(ctx.settings, list(targets.items())))
    typer.echo(f"Nudged {reached}/{len(targets)} unlinked entrants.")


if __name__ == "__main__":  # pragma: no cover
    app()
