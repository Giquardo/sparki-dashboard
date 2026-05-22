"""
SQLAlchemy declarative base + shared mixins.

All models in the app inherit from `Base`. Most also inherit from
`TimestampMixin` to get `created_at` and `updated_at` columns for free.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models.

    Alembic's `target_metadata = Base.metadata` is wired to this class
    in `migrations/env.py` (added in Step 2C).
    """


class TimestampMixin:
    """Adds `created_at` and `updated_at` UTC timestamps.

    `created_at` is set by Postgres DEFAULT clause on INSERT.
    `updated_at` is updated by SQLAlchemy ORM on every UPDATE,
    plus a DB-level trigger could be added later for raw SQL updates.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
