"""
Buildings business logic.

Two responsibilities here:
  1. Translate building IDs + time ranges into Flux queries.
  2. Shape the InfluxDB results into Pydantic API schemas.

Routes call these functions; this module never touches HTTP itself.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.buildings.schemas import (
    BuildingCurrent,
    BuildingHistory,
    BuildingHistoryPoint,
)
from app.config import settings
from app.influx import query_flux

logger = logging.getLogger("sparki.buildings.service")


# ─── Constants ───────────────────────────────────────────────────────
# Fields we return in the "current" snapshot. Must match the field
# names that Node-RED writes (see node-red/flows/flows.json).
CURRENT_FIELDS = (
    "pv_kw",
    "load_kw",
    "ev_charger_kw",
    "heatpump_kw",
    "battery_kw",
    "battery_soc",
    "grid_kw",
    "export_kw",
    "import_kw",
    "self_consumption_kw",
)

# Compact field set for history endpoints (less data over the wire).
HISTORY_FIELDS = ("pv_kw", "load_kw", "battery_kw", "battery_soc", "grid_kw")


def _flux_string_array(values) -> str:
    """Render a Python iterable of strings as a Flux array literal.

    Python's repr() uses single quotes; Flux requires double quotes for
    strings. We escape any embedded double quotes for safety, although
    our field names never contain them.

    >>> _flux_string_array(["pv_kw", "load_kw"])
    '["pv_kw", "load_kw"]'
    """
    escaped = (v.replace('"', '\\"') for v in values)
    inner = ", ".join(f'"{v}"' for v in escaped)
    return f"[{inner}]"


# ─── Current snapshot ────────────────────────────────────────────────
async def get_latest_for_building(building_id: uuid.UUID) -> BuildingCurrent:
    """Return the most recent value of each tracked field for one building.

    Strategy: a single Flux query over the last 10 minutes that pivots
    the long-format measurements into wide-format columns, then takes
    the last row per field. We assume data flows in regularly; if a
    building hasn't reported in >10 min, fields will be None.
    """
    bucket = settings.influxdb_bucket
    fields_array = _flux_string_array(CURRENT_FIELDS)

    # The `last()` aggregation per field gives us the freshest value
    # even if different fields have slightly different timestamps.
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -10m)
  |> filter(fn: (r) => r["_measurement"] == "sigen")
  |> filter(fn: (r) => r["building_id"] == "{building_id}")
  |> filter(fn: (r) => contains(value: r["_field"], set: {fields_array}))
  |> last()
'''

    rows = await query_flux(flux)

    # Build a dict from field name to (value, timestamp)
    field_values: dict[str, float] = {}
    latest_ts: datetime | None = None
    for record in rows:
        field = record.get_field()
        if field is None:
            continue
        value = record.get_value()
        ts = record.get_time()
        if value is not None:
            field_values[field] = float(value)
        if ts is not None and (latest_ts is None or ts > latest_ts):
            latest_ts = ts

    return BuildingCurrent(
        building_id=building_id,
        timestamp=latest_ts,
        **{field: field_values.get(field) for field in CURRENT_FIELDS},
    )


# ─── History ─────────────────────────────────────────────────────────
async def get_history(
    building_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    interval_seconds: int = 60,
) -> BuildingHistory:
    """Return a time-series for the given building.

    Defaults to the last 24 hours with 60-second aggregation if no
    range is provided. The mean aggregation function is used for each
    window — fine for power data, but we'd want sum() for energy
    counters (added later when those measurements exist).
    """
    now = datetime.now(timezone.utc)
    if end is None:
        end = now
    if start is None:
        start = end - timedelta(hours=24)

    # Flux pivot turns the long-format result into one row per timestamp
    # with one column per field — exactly what we want for chart points.
    bucket = settings.influxdb_bucket
    fields_array = _flux_string_array(HISTORY_FIELDS)
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {start.isoformat()}, stop: {end.isoformat()})
  |> filter(fn: (r) => r["_measurement"] == "sigen")
  |> filter(fn: (r) => r["building_id"] == "{building_id}")
  |> filter(fn: (r) => contains(value: r["_field"], set: {fields_array}))
  |> aggregateWindow(every: {interval_seconds}s, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''

    rows = await query_flux(flux)

    points: list[BuildingHistoryPoint] = []
    for record in rows:
        ts = record.get_time()
        if ts is None:
            continue
        # After pivot, each record has all selected fields as columns
        # in record.values. Some may be None for windows with no data.
        v = record.values
        points.append(
            BuildingHistoryPoint(
                timestamp=ts,
                pv_kw=_safe_float(v.get("pv_kw")),
                load_kw=_safe_float(v.get("load_kw")),
                battery_kw=_safe_float(v.get("battery_kw")),
                battery_soc=_safe_float(v.get("battery_soc")),
                grid_kw=_safe_float(v.get("grid_kw")),
            )
        )

    return BuildingHistory(
        building_id=building_id,
        start=start,
        end=end,
        interval_seconds=interval_seconds,
        points=points,
    )


def _safe_float(value: object) -> float | None:
    """Convert Flux values to Python float, treating None and bad casts as None."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
