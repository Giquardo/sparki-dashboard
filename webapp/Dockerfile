# ═══════════════════════════════════════════════════════════════════════
# Sparki webapp — multi-stage Dockerfile met uv
# ═══════════════════════════════════════════════════════════════════════
# Stage 1 (builder): installeert dependencies in een venv via uv.
# Stage 2 (runtime): kopieert alleen de venv + app code → klein, snel.

# ─── Stage 1: builder ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv binary kopiëren uit de officiële image (sneller dan pip install uv)
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Eerst alleen de dependency-manifesten kopiëren → caching werkt beter.
# Code wijzigt vaak, dependencies zelden.
COPY pyproject.toml ./
# Lockfile is optioneel bij eerste build — uv genereert er een als hij ontbreekt.
COPY uv.lock* ./

# Dependencies installeren (zonder de app zelf, alleen externals)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# App code erbij. Geen tweede `uv sync` nodig: package=false in pyproject.toml,
# dus uv installeert alleen dependencies (al gebeurd boven), niet de webapp zelf.
# uvicorn start straks `app.main:app` direct vanuit de filesystem.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# ─── Stage 2: runtime ────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user voor security
RUN groupadd --system --gid 1000 sparki && \
    useradd  --system --uid 1000 --gid sparki --no-create-home sparki

# Healthcheck heeft curl nodig
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kopieer de pre-built venv + code van builder
COPY --from=builder --chown=sparki:sparki /app /app

# Venv binaries op de PATH zetten
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER sparki

EXPOSE 8000

# Healthcheck: container is healthy zodra /healthz 200 geeft
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Production-grade entrypoint via uvicorn.
# In dev kunnen we --reload toevoegen via docker-compose override of CMD.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
