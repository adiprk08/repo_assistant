# Repo Assistant service image — runs the API (`ra serve`) by default; override the
# command with `ra worker` for the ingestion worker. See docs/DEPLOYMENT.md.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# git is needed at runtime to clone repositories.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached unless the lockfile changes), then the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Drop privileges.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

CMD ["ra", "serve", "--host", "0.0.0.0", "--port", "8000"]
