"""
Central model registry.

Import this module from anywhere you need to make sure ALL SQLAlchemy
models are registered on the `Base.metadata`. Used by Alembic's
`migrations/env.py` (Step 2C) so autogenerate can detect every table.

Adding a new model? Add it here too.
"""

from __future__ import annotations

# noqa: F401 — these imports register the models on Base.metadata
from app.buildings.models import Building, BuildingAssignment  # noqa: F401
from app.core.audit import AuditAction, AuditLog, AuditStatus  # noqa: F401
from app.core.base import Base  # noqa: F401
from app.organizations.models import Organization, OrganizationType  # noqa: F401
from app.sites.models import Site  # noqa: F401
from app.users.models import User, UserRole  # noqa: F401

__all__ = [
    "Base",
    "Building",
    "BuildingAssignment",
    "AuditLog",
    "AuditAction",
    "AuditStatus",
    "Organization",
    "OrganizationType",
    "Site",
    "User",
    "UserRole",
]
