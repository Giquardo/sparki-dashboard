"""
Alembic migration environment.

This file is invoked by `alembic upgrade head`, `alembic revision`, etc.
It overrides the default sqlalchemy.url with our settings, and points
target_metadata at app.models.Base so autogenerate can find every table.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ─── Make `app` importable when running `alembic ...` from /app ──────
# Inside the container, the working dir is /app and `app` is a sibling.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import settings + models AFTER sys.path is set
from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402  — pulls every model onto Base.metadata

# ─── Alembic Config object ───────────────────────────────────────────
config = context.config

# Override the static URL from alembic.ini with our runtime settings.
# We use the SYNC DSN here — Alembic operates synchronously by default,
# which is fine for one-shot migrations and keeps env.py simple.
config.set_main_option("sqlalchemy.url", settings.postgres_dsn_sync)

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what Alembic compares against the live DB to generate diffs.
target_metadata = Base.metadata


# ─── Offline migrations (--sql output) ───────────────────────────────
def run_migrations_offline() -> None:
    """Generate SQL instead of executing against a live DB.

    Useful for handover to DBAs: `alembic upgrade head --sql > upgrade.sql`.
    """
    context.configure(
        url=settings.postgres_dsn_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,           # detect column type changes
        compare_server_default=True, # detect server-default changes
    )

    with context.begin_transaction():
        context.run_migrations()


# ─── Online migrations (normal use) ──────────────────────────────────
def run_migrations_online() -> None:
    """Execute migrations against the live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
