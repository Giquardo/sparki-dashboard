"""
Prices business logic.

Two query patterns supported in v1:
  - `get_price_series(zone, start, end)` — for chart overlays
  - `get_current_price(zone)` — for the live tile on the dashboard
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.influx import query_flux
from app.prices.schemas import PricePoint, PriceSeries

logger = logging.getLogger("sparki.prices.service")


# ─── Series for chart overlay ────────────────────────────────────────
async def get_price_series(
    *,
    zone: str = "BE",
    start: datetime | None = None,
    end: datetime | None = None,
) -> PriceSeries:
    """Return the day-ahead price series for a zone over a time window.

    Defaults: last 24 hours up to now + 24 hours ahead (so the chart
    can show both 'what happened' and 'what's coming').

    If both `source=entsoe` and `source=mock` are present (e.g. a
    deployment that recently got its token but mock data is still
    around), the entsoe rows are preferred — they're the truth.
    """
    now = datetime.now(timezone.utc)
    if start is None:
        start = now - timedelta(hours=24)
    if end is None:
        end = now + timedelta(hours=24)

    bucket = settings.influxdb_bucket
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r["_measurement"] == "price")
  |> filter(fn: (r) => r["_field"] == "eur_per_mwh")
  |> filter(fn: (r) => r["zone"] == "{zone}")
  |> sort(columns: ["_time"])
'''
    rows = await query_flux(flux)

    # If both entsoe + mock exist for the same timestamp, prefer entsoe.
    by_ts: dict[datetime, tuple[float, str]] = {}
    for record in rows:
        ts = record.get_time()
        value = record.get_value()
        source = record.values.get("source", "unknown")
        if ts is None or value is None:
            continue
        if ts in by_ts and by_ts[ts][1] == "entsoe" and source == "mock":
            continue  # don't downgrade entsoe → mock
        by_ts[ts] = (float(value), source)

    sorted_items = sorted(by_ts.items())
    points = [
        PricePoint(timestamp=ts, eur_per_mwh=value)
        for ts, (value, _) in sorted_items
    ]

    # Pick the dominant source for the response metadata
    sources = {src for (_, src) in by_ts.values()}
    response_source = (
        "entsoe" if "entsoe" in sources
        else "mock" if "mock" in sources
        else "unknown"
    )

    return PriceSeries(
        zone=zone, start=start, end=end,
        source=response_source, points=points,
    )


# ─── Current price tile ──────────────────────────────────────────────
async def get_current_price(zone: str = "BE") -> PricePoint | None:
    """Return the day-ahead price that applies to "right now".

    Day-ahead prices are published per hour, so we look for the most
    recent price point with a timestamp ≤ now within a 2-hour lookback.
    """
    now = datetime.now(timezone.utc)
    bucket = settings.influxdb_bucket
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -2h, stop: now())
  |> filter(fn: (r) => r["_measurement"] == "price")
  |> filter(fn: (r) => r["_field"] == "eur_per_mwh")
  |> filter(fn: (r) => r["zone"] == "{zone}")
  |> last()
'''
    rows = await query_flux(flux)
    if not rows:
        return None
    record = rows[0]
    ts = record.get_time()
    value = record.get_value()
    if ts is None or value is None:
        return None
    return PricePoint(timestamp=ts, eur_per_mwh=float(value))
