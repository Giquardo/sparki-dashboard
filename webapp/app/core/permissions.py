"""
Central permission rules for the buildings domain.

This module exposes a SINGLE public function: `buildings_visible_to`.
Every route, every query, every export should derive its visibility
from this one source of truth.

Why? A permission bug here leaks data between tenants — the worst kind
of bug in a multi-tenant platform. Centralizing the logic gives us:
  - ONE place to audit
  - ONE place to test exhaustively
  - ONE place to evolve (e.g. add a "consultant" role in v2)

Tests live in tests/test_permissions.py and cover every role + every
common scenario (correct user, wrong org, no assignments, deactivated).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.buildings.models import Building, BuildingAssignment
from app.sites.models import Site
from app.users.models import UserRole

logger = logging.getLogger("sparki.permissions")


async def buildings_visible_to(
    user: CurrentUser,
    db: AsyncSession,
) -> set[uuid.UUID]:
    """Return the set of building IDs this user is allowed to see.

    Rules (mirrors §2 of the project plan):

      sparki_staff  → all active buildings, across all organisations
      site_owner    → all active buildings in sites of the user's org
      tenant        → only buildings explicitly assigned to this user

    The function returns a `set` so route handlers can do O(1)
    membership checks like `if requested_id in visible: ...`.

    Inactive buildings are excluded for sparki_staff and site_owner;
    tenants still see their assigned buildings even if marked inactive
    (so a tenant whose building got deactivated still sees historical
    data — call it the "right to your own data" principle).

    Raises nothing on its own: an empty set is a valid result for a
    user with no visible buildings.
    """
    if user.role == UserRole.SPARKI_STAFF:
        return await _all_active_building_ids(db)

    if user.role == UserRole.SITE_OWNER:
        return await _building_ids_in_org(db, user.organization_id)

    if user.role == UserRole.TENANT:
        return await _building_ids_assigned_to_user(db, user.id)

    # Defensive: an unknown role gets zero access.
    logger.warning(
        "buildings_visible_to: unknown role %r for user %s — returning empty set",
        user.role, user.id,
    )
    return set()


# ─── Helpers (one query each) ────────────────────────────────────────
async def _all_active_building_ids(db: AsyncSession) -> set[uuid.UUID]:
    """Every active building in the system. For sparki_staff only."""
    stmt = select(Building.id).where(Building.active.is_(True))
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def _building_ids_in_org(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> set[uuid.UUID]:
    """All active buildings whose site belongs to this organization."""
    stmt = (
        select(Building.id)
        .join(Site, Building.site_id == Site.id)
        .where(
            Site.organization_id == organization_id,
            Building.active.is_(True),
            Site.active.is_(True),
        )
    )
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def _building_ids_assigned_to_user(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> set[uuid.UUID]:
    """Only buildings explicitly assigned to this user.

    Tenants are linked to buildings via the `building_assignments` join
    table. This supports multiple tenants per building (e.g. roommates)
    without schema changes.
    """
    stmt = select(BuildingAssignment.building_id).where(
        BuildingAssignment.user_id == user_id,
    )
    result = await db.execute(stmt)
    return set(result.scalars().all())
