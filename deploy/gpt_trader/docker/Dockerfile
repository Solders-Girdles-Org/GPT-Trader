# Multi-stage Dockerfile for GPT-Trader.
# Uses uv to sync dependencies into a dedicated virtualenv.

# Stage 1: Base runtime image shared by all stages.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Common tooling required across stages (curl used by docker-compose healthchecks).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user available to descendant stages.
RUN groupadd -r trader && useradd -r -g trader trader
WORKDIR /app

# Stage 2: Dependency builder (installs uv-managed deps into /opt/venv).
#
# The venv lives outside /app so bind-mounting the repo in docker-compose does not
# shadow installed dependencies.
FROM base AS dependencies

# Build toolchain for native-backed Python dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    make \
    wget \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Install only runtime dependencies (no dev extras) into the active venv.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --active

# Stage 3: Development environment (includes dev dependencies and the full source tree).
FROM base AS development
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=dependencies /usr/local/bin/uv /usr/local/bin/uv
COPY --chown=trader:trader . .
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    ENV=development

# Install dev deps (and the project) into the active venv for interactive use.
RUN uv sync --frozen --active

USER trader
CMD ["python", "-m", "gpt_trader.cli", "run", "--profile", "dev"]

# Stage 4: Testing environment (runs unit tests with coverage).
FROM development AS testing
RUN uv run pytest tests/unit/ --cov=src/gpt_trader --cov-report=term-missing

# Stage 5: Production build (minimal runtime footprint).
FROM base AS production

# Runtime libraries only (native deps needed by some wheels).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dependencies /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    ENV=production \
    LOG_LEVEL=INFO

# Application code and configuration.
COPY --chown=trader:trader src/ ./src/
COPY --chown=trader:trader config/ ./config/

# Prepare runtime directories.
RUN mkdir -p /app/logs /app/data /app/cache \
    && chown -R trader:trader /app

USER trader

EXPOSE 8080

ENTRYPOINT ["python", "-m", "gpt_trader.cli"]
CMD ["run", "--profile", "prod"]

# Stage 6: Security scanner (optional tooling layer).
FROM production AS security
USER root
RUN pip install --no-cache-dir pip-audit bandit
RUN pip-audit --strict \
    && bandit -r /app/src -f json -o /tmp/bandit-report.json
USER trader
