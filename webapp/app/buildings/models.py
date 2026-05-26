"""
Building and BuildingAssignment models.

A Building is the physical installation: one house or unit with its own
Sigenergy gear and own meter. This is where the actual measurements live
(via the `sigen_station_id` that Node-RED uses for polling).

A BuildingAssignment couples a User to a Building. Used to grant tenants
access to their own home. Modeled as a separate table (not a FK on User
or Building) so we can later add:
  - start_date / end_date for tenancy periods
  - multiple tenants in one building (e.g. roommates)
  - audit of who had access when
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.sites.models import Site
    from app.users.models import User


class Building(Base, TimestampMixin):
    __tablename__ = "buildings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # ─── Sigencloud identity ─────────────────────────────────────────
    # Unique-per-installation identifier from Sigencloud (Sigencloud calls
    # this `systemId`). Node-RED uses this in API calls. Unique across the
    # whole DB. To find it: mySigen app → Settings → tap three-dot icon.
    sigen_system_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True, index=True,
    )

    # ─── Installation specs (mostly informational) ───────────────────
    installed_kwp: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ─── Relationships ───────────────────────────────────────────────
    site: Mapped[Site] = relationship(back_populates="buildings")
    assignments: Mapped[list[BuildingAssignment]] = relationship(
        back_populates="building",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Building {self.name!r} system={self.sigen_system_id}>"


class BuildingAssignment(Base, TimestampMixin):
    """Couples a User (typically role=TENANT) to a Building.

    Why a separate table?
      - Allows multiple tenants per building (roommates) without schema change.
      - Allows tenancy history (just add start_date / end_date later).
      - Avoids putting role-specific state on the User model.
    """

    __tablename__ = "building_assignments"
    __table_args__ = (
        # Prevent the same user from being assigned twice to the same building.
        UniqueConstraint("building_id", "user_id", name="uq_assignment_building_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    building_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("buildings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ─── Relationships ───────────────────────────────────────────────
    building: Mapped[Building] = relationship(back_populates="assignments")
    user: Mapped[User] = relationship(back_populates="building_assignments")

    def __repr__(self) -> str:
        return f"<BuildingAssignment building={self.building_id} user={self.user_id}>"
