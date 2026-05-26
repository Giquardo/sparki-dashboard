"""
Buildings REST API.

Three endpoints:
  GET /api/buildings                    → list buildings (visible-to-user)
  GET /api/buildings/{id}/current       → latest measurement values
  GET /api/buildings/{id}/history       → time-series data

All three are authenticated AND filtered by `buildings_visible_to(user)`.
A user requesting a building outside their visibility set gets a 403,
which is also recorded in `audit_log` for GDPR compliance.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.buildings.models import Building
from app.buildings.schemas import (
    BuildingCurrent,
    BuildingHistory,
    BuildingOut,
)
from app.buildings.service import get_history, get_latest_for_building
from app.core.audit import AuditAction
from app.core.audit_service import log_access_allowed, log_access_denied
from app.core.permissions import buildings_visible_to
from app.database import get_session

logger = logging.getLogger("sparki.buildings.routes")

router = APIRouter(prefix="/api/buildings", tags=["buildings"])


# ─── List ────────────────────────────────────────────────────────────
@router.get(
    "",
    response_model=list[BuildingOut],
    summary="List buildings",
    description=(
        "Returns active buildings the current user is allowed to see. "
        "A tenant typically sees one; a site_owner sees the buildings "
        "of their organisation; sparki_staff sees everything."
    ),
)
async def list_buildings(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[Building]:
    """Return buildings visible to the current user."""
    visible = await buildings_visible_to(user, db)

    log_access_allowed(
        user, action=AuditAction.LIST, resource_type="building", request=request,
    )

    if not visible:
        return []

    stmt = (
        select(Building)
        .where(Building.id.in_(visible), Building.active.is_(True))
        .order_by(Building.name)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ─── Current snapshot ────────────────────────────────────────────────
@router.get(
    "/{building_id}/current",
    response_model=BuildingCurrent,
    summary="Latest measurement values",
    description=(
        "Returns the most recent value of each tracked field for one "
        "building. If a field hasn't been reported in the last 10 "
        "minutes, its value will be `null`. Returns 403 if the building "
        "is not in the user's visibility set."
    ),
)
async def get_current(
    request: Request,
    building_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> BuildingCurrent:
    """Return the latest snapshot for one building.

    Order of checks:
      1. Permission check (403 + audit_log row if not allowed)
      2. Existence check (404 if not in DB)
      3. Data query against InfluxDB
    """
    await _check_visibility(user, db, building_id, AuditAction.VIEW, request)
    await _ensure_building_exists(db, building_id)

    log_access_allowed(
        user, action=AuditAction.VIEW, resource_type="building",
        resource_id=building_id, request=request,
    )
    return await get_latest_for_building(building_id)


# ─── History ─────────────────────────────────────────────────────────
@router.get(
    "/{building_id}/history",
    response_model=BuildingHistory,
    summary="Time-series history",
    description=(
        "Returns aggregated time-series data for a building. Defaults "
        "to last 24 hours at 60-second resolution. Use `start`, `end`, "
        "and `interval_seconds` to customize the window. Returns 403 "
        "if the building is not in the user's visibility set."
    ),
)
async def get_building_history(
    request: Request,
    building_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start: Annotated[
        datetime | None,
        Query(description="UTC start time. Defaults to 24h ago."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="UTC end time. Defaults to now."),
    ] = None,
    interval_seconds: Annotated[
        int,
        Query(
            ge=10,
            le=3600,
            description="Aggregation window in seconds (10–3600).",
        ),
    ] = 60,
) -> BuildingHistory:
    """Return a time-series for a building."""
    await _check_visibility(user, db, building_id, AuditAction.VIEW, request)
    await _ensure_building_exists(db, building_id)

    if start is not None and end is not None:
        if end <= start:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="`end` must be after `start`.",
            )
        if (end - start) > timedelta(days=30):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Requested range exceeds the 30-day maximum.",
            )

    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    log_access_allowed(
        user, action=AuditAction.VIEW, resource_type="building.history",
        resource_id=building_id, request=request,
    )
    return await get_history(
        building_id,
        start=start,
        end=end,
        interval_seconds=interval_seconds,
    )


# ─── Helpers ─────────────────────────────────────────────────────────
async def _check_visibility(
    user: CurrentUser,
    db: AsyncSession,
    building_id: uuid.UUID,
    action: AuditAction,
    request: Request,
) -> None:
    """Raise 403 if `building_id` is not visible to `user`, logging the denial.

    Why 403 and not 404: returning 404 would leak the existence of
    buildings the user can't see (timing-based enumeration attacks).
    Always returning 403 makes existence opaque.
    """
    visible = await buildings_visible_to(user, db)
    if building_id not in visible:
        await log_access_denied(
            user=user,
            action=action,
            resource_type="building",
            resource_id=building_id,
            request=request,
            detail=f"user role={user.role.value} not authorized for this building",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this building.",
        )


async def _ensure_building_exists(
    db: AsyncSession,
    building_id: uuid.UUID,
) -> Building:
    """Look up a building by ID, raising 404 if not found.

    Called *after* visibility check, so a 404 here means the user is
    allowed to see this building but it doesn't exist (e.g. URL typo).
    """
    result = await db.execute(select(Building).where(Building.id == building_id))
    building = result.scalar_one_or_none()
    if building is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building {building_id} not found",
        )
    return building
