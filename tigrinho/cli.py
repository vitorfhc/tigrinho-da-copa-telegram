"""Typer admin CLI (COMPLETION.md §13).

Run inside the container: ``docker compose exec bot python -m tigrinho.cli <command>``. Shares the
repositories + domain + settlement/scoreboard code with the bot. Destructive commands require an
explicit ``--yes`` flag. Output is plain aligned tables so it stays readable over ``compose exec``.

Capability groups (§13):
  1. CRUD games/bets/players  — ``games``/``bets``/``players`` sub-commands
  2. manual result & re-settle — ``set-result`` (idempotent)
  3. force sync & cache ops    — ``sync``, ``squads seed/refresh``, ``budget``
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

from tigrinho.board_data import load_board_records
from tigrinho.config import Settings, get_settings
from tigrinho.db.engine import create_db_engine, create_session_factory
from tigrinho.db.models import GameStatus
from tigrinho.db.models import SquadPlayer as DbSquadPlayer
from tigrinho.db.repositories import (
    ApiUsageRepository,
    BetRepository,
    GameRepository,
    PlayerRepository,
    SquadRepository,
)
from tigrinho.providers.base import FootballProvider, GoalEvent, MatchResult, SquadPlayer
from tigrinho.providers.budget import RequestBudget
from tigrinho.providers.factory import make_provider
from tigrinho.scoreboard import rank
from tigrinho.settlement_service import settle_fixture

_DUMPABLE_TABLES = ("players", "games", "bets", "squad_players", "api_usage")


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
squads_app = typer.Typer(no_args_is_help=True, help="Cached squads")
app.add_typer(games_app, name="games")
app.add_typer(bets_app, name="bets")
app.add_typer(players_app, name="players")
app.add_typer(squads_app, name="squads")


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
    scorer: Annotated[int | None, typer.Option(help="First-scorer player id")] = None,
    advancing: Annotated[int | None, typer.Option(help="Advancing team id (knockout)")] = None,
) -> None:
    """Set/override a game's 90′ score (+ optional first scorer / advancing team) and re-settle."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        game = GameRepository(session).get(fixture_id)
        if game is None:
            typer.echo(f"Game {fixture_id} not found.")
            raise typer.Exit(code=1)
        goals: tuple[GoalEvent, ...] = ()
        if scorer is not None:
            goals = (
                GoalEvent(
                    minute=1,
                    team_id=game.home_team_id,
                    player_id=scorer,
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
        )
        summary = settle_fixture(session, game, result)
        session.commit()
    typer.echo(
        f"Settled #{fixture_id}: {summary.home_team_name} {home} x {away} "
        f"{summary.away_team_name}; {len(summary.players)} player(s) graded."
    )


# --- group 3: force sync & cache ops --------------------------------------------------------


async def _seed_squad(ctx: CliContext, team_id: int) -> list[SquadPlayer]:
    return await ctx.budget.guarded(lambda: ctx.provider.get_squad(team_id))


def _to_orm_squad(players: list[SquadPlayer]) -> list[DbSquadPlayer]:
    return [
        DbSquadPlayer(player_id=p.player_id, team_id=p.team_id, name=p.name, position=p.position)
        for p in players
    ]


@squads_app.command("seed")
def squads_seed(team_id: int) -> None:
    """Fetch and cache a team's squad (via the provider + budget)."""
    ctx = build_cli_context()
    players = asyncio.run(_seed_squad(ctx, team_id))
    with ctx.session_factory() as session:
        SquadRepository(session).upsert_many(_to_orm_squad(players))
        session.commit()
    typer.echo(f"Seeded {len(players)} players for team {team_id}.")


@squads_app.command("refresh")
def squads_refresh() -> None:
    """Re-seed squads for every team that appears in the games table."""
    ctx = build_cli_context()
    with ctx.session_factory() as session:
        team_ids = sorted(
            {g.home_team_id for g in GameRepository(session).list_all()}
            | {g.away_team_id for g in GameRepository(session).list_all()}
        )
    total = 0
    for team_id in team_ids:
        players = asyncio.run(_seed_squad(ctx, team_id))
        with ctx.session_factory() as session:
            SquadRepository(session).upsert_many(_to_orm_squad(players))
            session.commit()
        total += len(players)
    typer.echo(f"Refreshed {total} players across {len(team_ids)} teams.")


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


if __name__ == "__main__":  # pragma: no cover
    app()
