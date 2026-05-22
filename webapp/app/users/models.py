"""
User domain model.

The User table mirrors the user identities in Keycloak. The primary key
is the Keycloak user UUID — so there's no need to maintain a separate
mapping table. The Keycloak JWT carries the `sub` claim, which we use
to look up the local User row on every request.

We store role + organization_id locally for fast permission checks
without round-tripping to Keycloak on every API call.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.buildings.models import BuildingAssignment
    from app.organizations.models import Organization


class UserRole(str, enum.Enum):
    """The three roles defined in the project plan.

    SPARKI_STAFF:  global access across all orgs/sites/buildings
    SITE_OWNER:    access to all sites/buildings within their own organization
    TENANT:        access to buildings explicitly assigned via BuildingAssignment
    """

    SPARKI_STAFF = "sparki_staff"
    SITE_OWNER = "site_owner"
    TENANT = "tenant"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    # Primary key = Keycloak's `sub` UUID. No separate mapping needed.
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )

    # ─── Relationships ───────────────────────────────────────────────
    organization: Mapped[Organization] = relationship(back_populates="users")
    building_assignments: Mapped[list[BuildingAssignment]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User {self.email!r} role={self.role.value}>"
