"""
Database connection setup.

Provides:
  - `engine`         : the async SQLAlchemy engine (singleton)
  - `AsyncSessionLocal`: factory for new sessions
  - `get_session()`  : FastAPI dependency that yields a session per request

Usage in a route:
    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import get_session

    @app.get("/things")
    async def list_things(db: AsyncSession = Depends(get_session)):
        ...
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger("sparki.database")


# ─── Engine ──────────────────────────────────────────────────────────
# Single engine for the whole app. Pool sized for ~20 concurrent requests;
# tune in production based on real load.
engine: AsyncEngine = create_async_engine(
    settings.postgres_dsn,
    echo=False,                        # set True for SQL trace during local debug
    pool_pre_ping=True,                # detect dropped connections automatically
    pool_size=10,
    max_overflow=10,
    pool_recycle=1800,                 # recycle connections every 30 min
)


# ─── Session factory ─────────────────────────────────────────────────
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,            # keeps objects usable after commit
    autoflush=False,
)


# ─── FastAPI dependency ──────────────────────────────────────────────
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for a single request, then close it.

    Rolls back on exceptions automatically; commits must be explicit
    in route/service code.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ─── Lifespan helpers ────────────────────────────────────────────────
async def dispose_engine() -> None:
    """Close all pooled connections. Call from FastAPI lifespan shutdown."""
    logger.info("Disposing database engine and pool")
    await engine.dispose()


async def ping_db() -> bool:
    """Return True if a SELECT 1 succeeds.

    Used by the deep-healthcheck endpoint (added in Step 2C).
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional for healthcheck
        logger.warning("Database ping failed: %s", exc)
        return False
