"""
HTML routes for the buildings/portfolio UI (Step 3.3).

Two routes:
  GET /                       → full portfolio page (card shell per building)
  GET /buildings/{id}/tile    → HTMX fragment: the live-data part of one card

Both reuse the SAME visibility logic as the JSON API
(`buildings_visible_to`). The tile route enforces a 403 + audit row for
any building outside the user's visibility set, exactly like
`/api/buildings/{id}/current` does — a tenant cannot read another
building's live tile.

Why a separate tile route instead of rendering everything in `/`:
  - The portfolio page paints instantly (one Postgres query), then each
    card lazy-loads its own live values via HTMX `hx-trigger="load"`.
  - Each card self-refreshes every 30s (`hx-trigger="... every 30s"`),
    matching decision-log #2 (30s polling for live tiles).
  - A slow/unavailable InfluxDB degrades one card, not the whole page.

Note: the home route ("/") was previously defined in app/web/routes.py
as a placeholder. In Step 3.3 it moves here so the buildings UI owns it.
The routes.py version must be removed (see the delivery notes).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.buildings.models import Building
from app.buildings.service import get_latest_for_building
from app.core.audit import AuditAction
from app.core.audit_service import log_access_denied
from app.core.permissions import buildings_visible_to
from app.database import get_session
from app.web.session import get_session_user_optional, get_session_user_required
from app.web.templates_env import template_context, templates

logger = logging.getLogger("sparki.web.buildings")

router = APIRouter(tags=["web"], include_in_schema=False)


# ─── Portfolio (home for logged-in users) ────────────────────────────
@router.get("/", response_class=HTMLResponse, name="home")
async def portfolio(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(get_session_user_optional)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Site root.

    Anonymous → landing splash.
    Logged in → portfolio page: one card shell per visible building.
                The cards fetch their own live data via HTMX.
    """
    if user is None:
        return templates.TemplateResponse(
            request,
            "pages/landing.html",
            template_context(request, user=None),
        )

    # Same visibility set the JSON API uses — single source of truth.
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
            request,
            user=user,
            page_title="Portfolio",
            buildings=buildings,
        ),
    )


# ─── Live-data tile fragment (HTMX target) ───────────────────────────
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
    """Return the live-data portion of one building card as an HTML fragment.

    Permission model mirrors /api/buildings/{id}/current:
      1. visibility check → 403 + audit row if not allowed
      2. fetch latest snapshot from InfluxDB
      3. render the fragment

    HTMX swaps this fragment into the card body and re-requests every 30s.
    """
    visible = await buildings_visible_to(user, db)
    if building_id not in visible:
        await log_access_denied(
            user=user,
            action=AuditAction.VIEW,
            resource_type="building.tile",
            resource_id=building_id,
            request=request,
            detail=f"user role={user.role.value} not authorized for this building tile",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this building.",
        )

    # Latest snapshot (may have None fields if no recent data).
    current = await get_latest_for_building(building_id)

    return templates.TemplateResponse(
        request,
        "partials/building_tile.html",
        template_context(request, user=user, current=current),
    )


__all__ = ["router"]
