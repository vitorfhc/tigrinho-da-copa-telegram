#!/usr/bin/env bash
# Container entrypoint (COMPLETION.md §15): apply migrations, then launch the bot.
set -euo pipefail

echo "Running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "Starting TigrinhoDaCopa bot (long polling)..."
exec python -m tigrinho
