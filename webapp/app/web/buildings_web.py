"""
HTML routes for the buildings / portfolio UI.

Page structure (Step 3.6 restructure):
  GET /                       → Portfolio: per-site summary cards (overview)
  GET /buildings              → Gebouwen: the building card grid (drill-down)
  GET /buildings/{id}         → building detail page
  GET /buildings/{id}/tile         → compact 3-metric card tile (HTMX)
  GET /buildings/{id}/tile/full    → full live-metric set (HTMX)
  GET /buildings/{id}/history.json → time-series JSON for Chart.js
  GET /prices/{zone}.json          → day-ahead price series JSON for Chart.js
  GET /sites/{id}/live.json        → aggregate live PV for a site (HTMX/JSON)

Portfolio (/) is the executive overview: one card per site the user can
see, with building count + total PV/battery capacity (from Postgres,
instant) and an aggregate "live PV now" figure (lazy-loaded).

Gebouwen (/buildings) is the operational view: the per-building card grid
that used to live at "/".

All building-scoped routes reuse `buildings_visible_to()` — the SAME
visibility logic as the JSON API — and emit 403 + audit row for any
building outside the user's set.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

_ALLOWED_ZONES: set[str] = {"BE", "NL", "DE-LU"}


# ─── Shared guards ───────────────────────────────────────────────────
async def _require_visible_building(
    user: CurrentUser,
    db: AsyncSession,
    building_id: uuid.UUID,
    request: Request,
    *,
    resource_type: str,
) -> None:
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
    result = await db.execute(select(Building).where(Building.id == building_id))
    building = result.scalar_one_or_none()
    if building is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building {building_id} not found",
        )
    return building


async def _visible_buildings(user: CurrentUser, db: AsyncSession) -> list[Building]:
    """Active buildings visible to the user, ordered by name."""
    visible = await buildings_visible_to(user, db)
    if not visible:
        return []
    stmt = (
        select(Building)
        .where(Building.id.in_(visible), Building.active.is_(True))
        .order_by(Building.name)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ─── Portfolio (/) — per-site summary ────────────────────────────────
@router.get("/", response_class=HTMLResponse, name="home")
async def portfolio(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Site root.

    Anonymous → landing splash.
    Logged in → Portfolio: one summary card per site the user can see,
                with building count + capacity totals. Live PV per site
                is lazy-loaded via /sites/{id}/live.json.
    """
    if user is None:
        return templates.TemplateResponse(
            request,
            "pages/landing.html",
            template_context(request, user=None),
        )

    buildings = await _visible_buildings(user, db)

    # Group visible buildings by site, accumulating totals.
    # site_id → {site, count, kwp, kwh}
    site_ids = {b.site_id for b in buildings}
    sites_by_id: dict[uuid.UUID, Site] = {}
    if site_ids:
        site_rows = await db.execute(select(Site).where(Site.id.in_(site_ids)))
        sites_by_id = {s.id: s for s in site_rows.scalars().all()}

    summaries: list[dict] = []
    grouped: dict[uuid.UUID, list[Building]] = {}
    for b in buildings:
        grouped.setdefault(b.site_id, []).append(b)

    for sid, bldgs in grouped.items():
        site = sites_by_id.get(sid)
        summaries.append({
            "site_id": sid,
            "site_name": site.name if site else "Onbekende site",
            "count": len(bldgs),
            "total_kwp": sum((b.installed_kwp or 0) for b in bldgs),
            "total_kwh": sum((b.battery_kwh or 0) for b in bldgs),
            "building_ids": [str(b.id) for b in bldgs],
        })
    # Stable order: by site name.
    summaries.sort(key=lambda s: s["site_name"])

    return templates.TemplateResponse(
        request,
        "pages/portfolio.html",
        template_context(
            request, user=user, page_title="Portfolio",
            summaries=summaries, total_buildings=len(buildings),
        ),
    )


# ─── Gebouwen (/buildings) — card grid ───────────────────────────────
@router.get("/buildings", response_class=HTMLResponse, name="buildings_list")
async def buildings_list(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """The building card grid (previously at "/")."""
    buildings = await _visible_buildings(user, db)
    return templates.TemplateResponse(
        request,
        "pages/buildings.html",
        template_context(
            request, user=user, page_title="Gebouwen", buildings=buildings,
        ),
    )


# ─── Per-site aggregate live PV (HTMX/JSON) ──────────────────────────
@router.get("/sites/{site_id}/live.json", name="site_live_json")
async def site_live_json(
    request: Request,
    site_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    """Aggregate live PV (kW) summed across the user's visible buildings
    in this site. Used by the Portfolio summary cards.

    Permission: we only sum buildings the user can actually see, so a
    user never learns about buildings outside their visibility even via
    the aggregate. If the site has no visible buildings → 403.
    """
    visible = await buildings_visible_to(user, db)
    # Which visible buildings belong to this site?
    stmt = (
        select(Building.id)
        .where(
            Building.site_id == site_id,
            Building.active.is_(True),
            Building.id.in_(visible) if visible else False,
        )
    )
    result = await db.execute(stmt)
    site_building_ids = list(result.scalars().all())

    if not site_building_ids:
        await log_access_denied(
            user=user,
            action=AuditAction.VIEW,
            resource_type="site.live",
            resource_id=site_id,
            request=request,
            detail=f"user role={user.role.value} has no visible buildings in this site",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No visible buildings in this site.",
        )

    total_pv = 0.0
    any_data = False
    for bid in site_building_ids:
        current = await get_latest_for_building(bid)
        if current.timestamp is not None and current.pv_kw is not None:
            total_pv += current.pv_kw
            any_data = True

    return JSONResponse(content={
        "site_id": str(site_id),
        "buildings": len(site_building_ids),
        "total_pv_kw": round(total_pv, 2) if any_data else None,
        "has_data": any_data,
    })


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
    await _require_visible_building(
        user, db, building_id, request, resource_type="building",
    )
    building = await _load_building(db, building_id)
    site_result = await db.execute(select(Site).where(Site.id == building.site_id))
    site = site_result.scalar_one_or_none()
    return templates.TemplateResponse(
        request,
        "pages/building_detail.html",
        template_context(
            request, user=user, page_title=building.name,
            building=building, site=site,
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
    await _require_visible_building(
        user, db, building_id, request, resource_type="building.history",
    )
    await _load_building(db, building_id)
    history = await get_history(building_id, interval_seconds=interval_seconds)
    return JSONResponse(content=history.model_dump(mode="json"))


@router.get("/prices/{zone}.json", name="prices_json")
async def prices_json(
    request: Request,
    zone: str,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
) -> JSONResponse:
    zone = zone.upper()
    if zone not in _ALLOWED_ZONES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown bidding zone {zone!r}. Allowed: {sorted(_ALLOWED_ZONES)}",
        )
    series = await get_price_series(zone=zone)
    return JSONResponse(content=series.model_dump(mode="json"))


__all__ = ["router"]

_ = selectinload  # reserved for future eager-loading of site.buildings
