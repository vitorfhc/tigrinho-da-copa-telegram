# TigrinhoDaCopa bot image (COMPLETION.md §15).
# python:3.12-slim, non-root, dependencies installed from pyproject.toml + uv.lock via uv.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    CONFIG_PATH=/app/config.yaml

# uv for fast, reproducible dependency installs.
COPY --from=ghcr.io/astral-sh/uv:0.9.20 /uv /usr/local/bin/uv

WORKDIR /app

# Install runtime dependencies first (cached unless the lockfile changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application and install the project itself.
COPY tigrinho ./tigrinho
COPY alembic.ini ./alembic.ini
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN uv sync --frozen --no-dev \
    && chmod +x ./docker/entrypoint.sh

# Non-root user; /data is the mounted SQLite volume.
RUN useradd --create-home --uid 1000 tigrinho \
    && mkdir -p /data \
    && chown -R tigrinho:tigrinho /app /data
USER tigrinho

# Runs `alembic upgrade head` then launches the bot (long polling; no inbound ports).
ENTRYPOINT ["./docker/entrypoint.sh"]
