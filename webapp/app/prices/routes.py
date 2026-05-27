"""
Prices REST API.

Two endpoints:
  GET /api/prices/{zone}            → time-series of day-ahead prices
  GET /api/prices/{zone}/current    → current hourly price tile

Authentication: required (any authenticated role).
Permissions: prices are NOT building-specific — every authenticated
user sees the same market prices. There's no business reason to scope
them per-tenant.

The Belgian bidding zone is `BE`. Other supported zones (when configured
in Node-RED): `NL`, `DE-LU`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.prices.schemas import PricePoint, PriceSeries
from app.prices.service import get_current_price, get_price_series

logger = logging.getLogger("sparki.prices.routes")

router = APIRouter(prefix="/api/prices", tags=["prices"])


# Allowed zones — keep tight to prevent typos / fishing for data
# that doesn't exist. Add to this set as new zones are ingested.
_ALLOWED_ZONES: set[str] = {"BE", "NL", "DE-LU"}


def _validate_zone(zone: str) -> str:
    """Uppercase + validate. Raises 400 on unknown zone."""
    zone = zone.upper()
    if zone not in _ALLOWED_ZONES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown bidding zone {zone!r}. Allowed: {sorted(_ALLOWED_ZONES)}",
        )
    return zone


@router.get(
    "/{zone}",
    response_model=PriceSeries,
    summary="Day-ahead price series",
    description=(
        "Returns hourly day-ahead prices for the given bidding zone. "
        "Default range: last 24h up to next 24h (so a chart shows both "
        "past and upcoming prices)."
    ),
)
async def get_series(
    zone: str,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    start: Annotated[
        datetime | None,
        Query(description="UTC start. Defaults to 24h ago."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="UTC end. Defaults to now + 24h."),
    ] = None,
) -> PriceSeries:
    zone = _validate_zone(zone)

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

    return await get_price_series(zone=zone, start=start, end=end)


@router.get(
    "/{zone}/current",
    response_model=PricePoint,
    summary="Current hourly price",
    description=(
        "Returns the day-ahead price for the current hour. 404 if no "
        "price has been ingested in the past 2 hours."
    ),
)
async def get_current(
    zone: str,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
) -> PricePoint:
    zone = _validate_zone(zone)
    price = await get_current_price(zone=zone)
    if price is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No recent price for zone {zone}",
        )
    return price
