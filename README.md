# 🐯 TigrinhoDaCopa (Telegram)

A Telegram bot for **friendly, no-money** bets on the **FIFA World Cup 2026**, played inside one
group of friends (the "Tigrinhos"). The bot announces newly-scheduled games to the **group**,
collects predictions **privately** in each player's chat with the bot, grades them automatically
when each game ends (on the **90′** result), awards points, and keeps an all-time and a weekly
scoreboard.

> ⚠️ **No real money.** This is for bragging rights only. Not affiliated with FIFA or any betting
> service.

---

## 1. Overview

- **Where things happen:** the bot posts **announcements, results, and the scoreboard** to your
  group; **all betting happens in the bot's private chat** (Telegram has no self-only group
  messages). Players reach the wizard by tapping **🎯 Apostar** on an announcement (a deep link) or
  by sending `/apostar` to the bot in private.
- **No roles, no subscriptions:** everyone in the group already receives the bot's posts.
- **Bet categories:** exact score (5 pts), first team to score (3), both teams to score (2), winner (2),
  over/under 2.5 (1). All graded on the **90-minute** regulation result; knockout "winner" is the
  team that advances (no draw).
- **Self-hosted** with Docker; uses long polling (no public URL needed).

## 2. Prerequisites

- **Docker** and **Docker Compose** (Compose v2: `docker compose …`).
- A **Telegram account** and a **Telegram group** (or supergroup) for your friends.
- An **API-Football** account (free tier is enough for one group) — https://www.api-football.com/.

## 3. Create the Telegram bot

1. In Telegram, talk to **@BotFather** → `/newbot`. Choose a name and a **username** (must end in
   `bot`, e.g. `TigrinhoDaCopaBot`).
2. Copy the **bot token** BotFather gives you (looks like `123456:ABC-DEF...`).
3. Optionally set a description/about text. Commands are registered automatically by the bot at
   startup (`setMyCommands`), so you don't need `/setcommands`.
4. Note the bot's **`@username`** (without the `@`) — you'll put it in `config.yaml`.

## 4. Group & IDs

1. **Add the bot to your group.** It needs **no admin rights** (it only posts its own messages;
   betting is in DM). Privacy mode may stay enabled.
2. **Get the `group_chat_id`** (a negative number for groups/supergroups). Easiest: add
   **@userinfobot** to the group, or temporarily log `update.effective_chat.id`. Supergroup ids look
   like `-1001234567890`.
3. **Get your `admin_user_id`** (your own Telegram user id) — e.g. message **@userinfobot** in
   private.
4. **Press Start** in the bot's private chat — **the admin and every player must do this once** so
   the bot is allowed to DM them. Players are prompted automatically the first time they tap a
   deep-link button.

## 5. Get the API-Football key

1. Sign up at https://www.api-football.com/ and copy your **API key**.
2. Note the **free-tier daily request limit** (commonly 100/day). The bot enforces a hard cap
   (`api_daily_cap`, default **100**) and resets the counter at `api_budget_reset_tz` midnight
   (API-Football resets at 00:00 UTC).
3. **Verify the World Cup 2026 league id & season** against the current API: query the `leagues`
   endpoint and confirm the league id (the spec default is `wc_league_id: 1`, `wc_season: 2026` —
   re-check before the tournament and adjust `config.yaml`).

## 6. Configure

```bash
cp .env.example .env                 # fill the two secrets
cp config.example.yaml config.yaml   # fill group_chat_id, admin_user_id, bot_username
```

- **`.env`** (secrets only — never commit):
  - `TELEGRAM_BOT_TOKEN` — from BotFather
  - `API_FOOTBALL_KEY` — from API-Football
- **`config.yaml`** (everything else). Settings reference:

  | Key | Required | Default | Purpose |
  |---|---|---|---|
  | `group_chat_id` | yes | — | The group the bot serves (negative int). |
  | `admin_user_id` | yes | — | User DM'd on errors/limits. |
  | `bot_username` | yes | — | Bot `@username` (no `@`); verified at startup. |
  | `provider_mode` | no | `api_football` | `api_football` or `fake` (local/dev). |
  | `api_football_base_url` | no | `https://v3.football.api-sports.io` | Provider base URL. |
  | `wc_league_id` | no | `1` | FIFA World Cup league id (verify!). |
  | `wc_season` | no | `2026` | Season. |
  | `timezone` | no | `America/Sao_Paulo` | Sync time, displayed kickoffs, weekly reset. |
  | `sync_time` | no | `06:00` | Daily fixtures sync (local time). |
  | `poll_interval_minutes` | no | `10` | Live-poll cadence during matches. |
  | `match_window_hours` | no | `3` | How long after kickoff a game stays "active". |
  | `api_daily_cap` | no | `100` | Hard ceiling on provider requests/day. |
  | `api_budget_reset_tz` | no | `UTC` | Timezone whose midnight resets the counter. |
  | `db_path` | no | `/data/tigrinho.db` | SQLite path (mounted volume). |
  | `log_level` | no | `INFO` | Log level. |
  | `log_format` | no | `json` | `json` or `console`. |

## 7. Run

```bash
docker compose up -d --build
docker compose logs -f          # watch startup
```

On start the container runs `alembic upgrade head` (creating/upgrading the SQLite DB), validates the
config against the live bot (`get_me().username` must match `bot_username`, and the group must be
reachable — otherwise it fails fast), registers slash commands, and begins long polling. The logs
should show `startup_validated` and `starting`.

## 8. First-run setup

No squad seeding is needed (the first-scorer market is **team-based**). Optionally force a sync now
to populate games instead of waiting for the daily job:

```bash
docker compose exec bot python -m tigrinho.cli sync
```

## 9. Player guide

All commands are in pt-BR (the players' language):

- **`/apostar`** — open the betting wizard (in the bot's private chat).
- **`/minhas_apostas`** — list and delete your bets (private).
- **`/jogos`** — upcoming games and what you still have to predict.
- **`/placar`** — the scoreboard (toggle **Geral** ↔ **Semana**).
- **`/ajuda`** — how the bolão works, categories, points, rules.
- **`/start`** — welcome (a `bet_<id>` deep link jumps straight into the wizard).

To bet: tap **🎯 Apostar** under a group announcement (opens the private chat), then pick a game →
category → your prediction. Bets are editable until kickoff; one bet per category per game.

## 10. Admin CLI

Run any command via `docker compose exec bot python -m tigrinho.cli <command>`:

```bash
# Group 1 — CRUD
python -m tigrinho.cli games list
python -m tigrinho.cli games show <FIXTURE_ID>
python -m tigrinho.cli games delete <FIXTURE_ID> --yes
python -m tigrinho.cli players list
python -m tigrinho.cli bets list --player <ID> --game <FIXTURE_ID>

# Group 2 — manual result & re-settle (idempotent)
python -m tigrinho.cli set-result <FIXTURE_ID> <HOME> <AWAY> --first-team home --advancing <TEAM_ID>

# Group 3 — sync & budget
python -m tigrinho.cli sync
python -m tigrinho.cli budget

# Group 4 — board & DB dump
python -m tigrinho.cli board --weekly
python -m tigrinho.cli db --table bets

# Setup helper
python -m tigrinho.cli telegram-info
```

Destructive commands (`delete`) require `--yes`.

## 11. Operations

- **Database:** lives in the named Docker volume mounted at `/data` (`db_path: /data/tigrinho.db`).
  Back it up by copying the file out:
  ```bash
  docker compose cp bot:/data/tigrinho.db ./backup-tigrinho.db
  ```
- **Logs:** `docker compose logs -f` (structured JSON by default; set `log_format: console` for
  local readability).
- **Admin alerts:** the bot DMs `admin_user_id` on sync failures, the daily API cap being reached
  (once per budget day), games that can't auto-settle, and unhandled errors in scheduled jobs.
- **API cap:** when the daily cap is hit, live polling pauses until the budget resets; the daily
  sync and full-time settlement reads take priority over polling.
- **Update / redeploy:** `git pull && docker compose up -d --build`. Migrations run automatically on
  restart; they are append-only (existing data is preserved).

## 12. Troubleshooting

- **Bot not posting to the group** — it was removed from the group, or `group_chat_id` is wrong
  (must be the negative supergroup id). Re-check with `telegram-info` and `update.effective_chat.id`.
- **Deep link doesn't open the wizard** — `bot_username` in `config.yaml` doesn't match the real
  bot; startup fails fast on a mismatch, so check the logs.
- **Admin/player not receiving DMs** — they never pressed **Start** in the bot's private chat.
- **Commands not appearing** — give Telegram a minute after first start; check the right
  `BotCommandScope` (private vs group). Restart to re-register.
- **No games showing** — wrong `wc_league_id`/`wc_season`; verify against the current API and
  re-run `sync`.
- **"API cap reached"** — you hit `api_daily_cap`; polling resumes after the reset.
- **Timezone surprises** — kickoffs/weekly reset use `timezone`; the budget resets at
  `api_budget_reset_tz` midnight.

## 13. Development

```bash
uv sync                         # create .venv and install deps (incl. dev)
uv run ruff check .             # lint
uv run ruff format --check .    # format check
uv run mypy --strict .          # type check
uv run pytest                   # tests (enforces 100% branch coverage on scoring + settlement)
```

Run the bot locally without API-Football by setting `provider_mode: fake` in `config.yaml` (the
`FakeProvider` serves scripted fixtures/results). The DB is plain SQLite — point `db_path` at a
local file for dev.

## 14. Disclaimer

Friendly bets only — **no real money**, no payouts. Not affiliated with FIFA, Telegram, or
API-Football. Use among consenting friends for fun. 🐯
