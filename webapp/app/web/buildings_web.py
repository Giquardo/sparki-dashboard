"""
HTML routes for the buildings/portfolio UI.

Step 3.3 routes:
  GET /                            → portfolio page (card shell per building)
  GET /buildings/{id}/tile         → HTMX fragment: 3-metric card tile

Step 3.4 routes:
  GET /buildings/{id}              → building detail page
  GET /buildings/{id}/tile/full    → HTMX fragment: full live-metric set
  GET /buildings/{id}/history.json → JSON for Chart.js (cookie-auth)
  GET /prices/{zone}.json          → JSON for Chart.js price overlay (cookie-auth)

All building-scoped routes reuse `buildings_visible_to()` — the SAME
visibility logic as the JSON API — and emit a 403 + audit row for any
building outside the user's set.

Why cookie-auth data routes (history.json / prices.json) instead of
letting Chart.js call /api/*: the JSON API authenticates via Bearer
token, but the browser only carries the session cookie. Rather than
expose a token to JS, we add thin web-side data endpoints that share
the session-cookie dependency and call the SAME service functions the
API uses. The JSON API stays pure (Bearer-only, untouched).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.buildings.models import Building
from app.buildings.service import get_history, get_latest_for_building
from app.core.audit import AuditAction
from app.core.audit_service import log_access_denied
from app.core.permissions import buildings_visible_to
from app.database import get_session
from app.prices.service import get_price_series
from app.sites.models import Site
from app.web.session import get_session_user_optional, get_session_user_required
from app.web.templates_env import template_context, templates

logger = logging.getLogger("sparki.web.buildings")

router = APIRouter(tags=["web"], include_in_schema=False)

# Zones we allow the price overlay to request (mirrors prices API).
_ALLOWED_ZONES: set[str] = {"BE", "NL", "DE-LU"}


# ─── Shared visibility guard ─────────────────────────────────────────
async def _require_visible_building(
    user: CurrentUser,
    db: AsyncSession,
    building_id: uuid.UUID,
    request: Request,
    *,
    resource_type: str,
) -> None:
    """Raise 403 (+ audit row) if building_id is not visible to user."""
    visible = await buildings_visible_to(user, db)
    if building_id not in visible:
        await log_access_denied(
            user=user,
            action=AuditAction.VIEW,
            resource_type=resource_type,
            resource_id=building_id,
            request=request,
            detail=f"user role={user.role.value} not authorized for this building",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this building.",
        )


async def _load_building(db: AsyncSession, building_id: uuid.UUID) -> Building:
    """Fetch a building or raise 404. Call AFTER the visibility check."""
    result = await db.execute(select(Building).where(Building.id == building_id))
    building = result.scalar_one_or_none()
    if building is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building {building_id} not found",
        )
    return building


# ─── Portfolio (home for logged-in users) ────────────────────────────
@router.get("/", response_class=HTMLResponse, name="home")
async def portfolio(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Anonymous → landing splash. Logged in → portfolio of visible buildings."""
    if user is None:
        return templates.TemplateResponse(
            request,
            "pages/landing.html",
            template_context(request, user=None),
        )

    visible = await buildings_visible_to(user, db)
    buildings: list[Building] = []
    if visible:
        stmt = (
            select(Building)
            .where(Building.id.in_(visible), Building.active.is_(True))
            .order_by(Building.name)
        )
        result = await db.execute(stmt)
        buildings = list(result.scalars().all())

    return templates.TemplateResponse(
        request,
        "pages/portfolio.html",
        template_context(
            request, user=user, page_title="Portfolio", buildings=buildings,
        ),
    )


# ─── Building detail page ────────────────────────────────────────────
@router.get(
    "/buildings/{building_id}",
    response_class=HTMLResponse,
    name="building_detail",
)
async def building_detail(
    request: Request,
    building_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Full detail page for one building: live tiles + history charts.

    Order: visibility check (403 + audit) → existence (404) → render.
    Charts fetch their data client-side from the .json routes below.
    """
    await _require_visible_building(
        user, db, building_id, request, resource_type="building",
    )
    building = await _load_building(db, building_id)

    # Resolve the site name for the header breadcrumb.
    site_result = await db.execute(select(Site).where(Site.id == building.site_id))
    site = site_result.scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "pages/building_detail.html",
        template_context(
            request,
            user=user,
            page_title=building.name,
            building=building,
            site=site,
        ),
    )


# ─── Live tile fragments ─────────────────────────────────────────────
@router.get(
    "/buildings/{building_id}/tile",
    response_class=HTMLResponse,
    name="building_tile",
)
async def building_tile(
    request: Request,
    building_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Compact 3-metric tile for a portfolio card (PV / battery / grid)."""
    await _require_visible_building(
        user, db, building_id, request, resource_type="building.tile",
    )
    current = await get_latest_for_building(building_id)
    return templates.TemplateResponse(
        request,
        "partials/building_tile.html",
        template_context(request, user=user, current=current),
    )


@router.get(
    "/buildings/{building_id}/tile/full",
    response_class=HTMLResponse,
    name="building_tile_full",
)
async def building_tile_full(
    request: Request,
    building_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Full live-metric set for the detail page (all 10 fields)."""
    await _require_visible_building(
        user, db, building_id, request, resource_type="building.tile",
    )
    current = await get_latest_for_building(building_id)
    return templates.TemplateResponse(
        request,
        "partials/building_tiles_full.html",
        template_context(request, user=user, current=current),
    )


# ─── Chart data routes (cookie-auth JSON for Chart.js) ───────────────
@router.get("/buildings/{building_id}/history.json", name="building_history_json")
async def building_history_json(
    request: Request,
    building_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
    interval_seconds: Annotated[int, Query(ge=60, le=3600)] = 300,
) -> JSONResponse:
    """Time-series history as JSON for Chart.js.

    Cookie-authenticated mirror of /api/buildings/{id}/history. Reuses
    the same `get_history()` service. Default interval is 300s (5 min)
    for a 24h window → ~288 points, smooth without being heavy.
    """
    await _require_visible_building(
        user, db, building_id, request, resource_type="building.history",
    )
    await _load_building(db, building_id)

    history = await get_history(building_id, interval_seconds=interval_seconds)
    # Pydantic model → JSON-serializable dict. mode="json" handles datetimes.
    return JSONResponse(content=history.model_dump(mode="json"))


@router.get("/prices/{zone}.json", name="prices_json")
async def prices_json(
    request: Request,
    zone: str,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
) -> JSONResponse:
    """Day-ahead price series as JSON for the chart overlay.

    Cookie-authenticated mirror of /api/prices/{zone}. Prices are market
    data — any authenticated user may see them (no building scope).
    """
    zone = zone.upper()
    if zone not in _ALLOWED_ZONES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown bidding zone {zone!r}. Allowed: {sorted(_ALLOWED_ZONES)}",
        )
    series = await get_price_series(zone=zone)
    return JSONResponse(content=series.model_dump(mode="json"))


__all__ = ["router"]

# Keep the import used; referenced for potential future server-side
# timestamp formatting.
_ = (datetime, timezone)
