"""
HTML route for the Prijzen (prices) page.

  GET /prices               → the prices page (chart + current price)
  GET /prices/{zone}/current.json → cookie-auth current price for the tile

The day-ahead price series JSON (/prices/{zone}.json) already lives in
buildings_web.py (added in Step 3.4 for the detail-page overlay); this
module reuses it for the standalone Prijzen page chart.

Prices are market data — every authenticated user may see them, so these
routes require a session but do NOT go through buildings_visible_to().
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth.schemas import CurrentUser
from app.prices.service import get_current_price
from app.web.session import get_session_user_required
from app.web.templates_env import template_context, templates

logger = logging.getLogger("sparki.web.prices")

router = APIRouter(tags=["web"], include_in_schema=False)

_ALLOWED_ZONES: set[str] = {"BE", "NL", "DE-LU"}


@router.get("/prices", response_class=HTMLResponse, name="prices_page")
async def prices_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
) -> HTMLResponse:
    """Render the standalone Prijzen page.

    The chart fetches /prices/BE.json client-side (same data route the
    building detail overlay uses) and the current-price tile fetches
    /prices/BE/current.json. Both refresh on a timer.
    """
    return templates.TemplateResponse(
        request,
        "pages/prices.html",
        template_context(request, user=user, page_title="Prijzen", zone="BE"),
    )


@router.get("/prices/{zone}/current.json", name="prices_current_json")
async def prices_current_json(
    request: Request,
    zone: str,
    user: Annotated[CurrentUser, Depends(get_session_user_required)],
) -> JSONResponse:
    """Current hourly price as JSON for the page's headline tile.

    Returns {"available": false} (200) when no recent price exists, so
    the UI can show a graceful "geen prijs" state instead of handling a
    404 in JS.
    """
    zone = zone.upper()
    if zone not in _ALLOWED_ZONES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown bidding zone {zone!r}. Allowed: {sorted(_ALLOWED_ZONES)}",
        )
    price = await get_current_price(zone=zone)
    if price is None:
        return JSONResponse(content={"available": False, "zone": zone})
    return JSONResponse(
        content={"available": True, "zone": zone, **price.model_dump(mode="json")}
    )


__all__ = ["router"]
