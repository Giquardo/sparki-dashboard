"""
Site domain model.

A Site is an administrative grouping of one or more Buildings — typically
a neighborhood, project, or building complex. A Site is NOT a physical
installation; it's a navigation and grouping layer.

Examples:
  - "Wijk Sint-Jan" — the 10 social-housing buildings in the city project
  - "Privé" — an automatic single-site for a private homeowner with 1 building
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.buildings.models import Building
    from app.organizations.models import Organization


class Site(Base, TimestampMixin):
    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # IANA timezone (e.g. "Europe/Brussels"). Used for daily aggregations.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Brussels")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ─── Relationships ───────────────────────────────────────────────
    organization: Mapped[Organization] = relationship(back_populates="sites")
    buildings: Mapped[list[Building]] = relationship(
        back_populates="site",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Site {self.name!r}>"
