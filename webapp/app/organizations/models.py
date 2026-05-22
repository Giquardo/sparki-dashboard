"""
Organization domain model.

An Organization is the top of the multi-tenant hierarchy. It owns:
  - one or more Sites (administrative groupings)
  - one or more Users (employees of the organization)
  - the encrypted Sigencloud credentials used by Node-RED to fetch data
    for all buildings under this organization

Examples of an Organization:
  - The Sparki organization itself (type=SPARKI)
  - A social housing authority (type=SITE_OWNER)
  - A private homeowner with multiple buildings (type=PRIVATE)
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.sites.models import Site
    from app.users.models import User


class OrganizationType(str, enum.Enum):
    """Categorizes organizations for billing, support, and UI affordances."""

    SPARKI = "sparki"          # the Sparki organization itself
    SITE_OWNER = "site_owner"  # a customer who owns one or more sites
    PRIVATE = "private"        # an individual homeowner


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    type: Mapped[OrganizationType] = mapped_column(
        Enum(OrganizationType, name="organization_type"),
        nullable=False,
    )

    # ─── Sigencloud account ──────────────────────────────────────────
    # One Sigencloud account can manage many stations, so credentials
    # live at the organization level (not per building).
    sigen_account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Fernet-encrypted bytes; decryption key lives in app config.
    sigen_credentials_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True,
    )

    # ─── Relationships ───────────────────────────────────────────────
    sites: Mapped[list[Site]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    users: Mapped[list[User]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Organization {self.name!r} ({self.type.value})>"
