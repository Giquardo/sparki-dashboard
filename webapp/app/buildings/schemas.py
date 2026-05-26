"""
Pydantic API schemas for the buildings domain.

These define the JSON shape of API responses. They are NOT the same
as the SQLAlchemy models (those define DB rows). Keeping them
separate lets us evolve the API without DB migrations and vice versa.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ─── List view ───────────────────────────────────────────────────────
class BuildingOut(BaseModel):
    """A single building as returned by GET /api/buildings."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    site_id: uuid.UUID
    sigen_system_id: str | None = None
    installed_kwp: float | None = None
    battery_kwh: float | None = None
    active: bool


# ─── Current snapshot ────────────────────────────────────────────────
class BuildingCurrent(BaseModel):
    """Latest measurement values for a building.

    Returned by GET /api/buildings/{id}/current.

    All field values are kW unless otherwise noted (`battery_soc` is %).
    Fields may be None if no recent measurement exists for that field.
    """

    building_id: uuid.UUID
    timestamp: datetime | None = Field(
        None,
        description="UTC timestamp of the most recent measurement (any field).",
    )

    # Field names mirror Sigencloud's energyFlow response, normalized to snake_case.
    pv_kw: float | None = None
    load_kw: float | None = None
    ev_charger_kw: float | None = None
    heatpump_kw: float | None = None
    battery_kw: float | None = Field(None, description="Positive = charging")
    battery_soc: float | None = Field(None, description="State of charge (%)")
    grid_kw: float | None = Field(None, description="Positive = selling to grid")
    export_kw: float | None = None
    import_kw: float | None = None
    self_consumption_kw: float | None = None


# ─── History point ───────────────────────────────────────────────────
class BuildingHistoryPoint(BaseModel):
    """One point in a time-series response.

    Returned as part of GET /api/buildings/{id}/history.
    """

    timestamp: datetime
    pv_kw: float | None = None
    load_kw: float | None = None
    battery_kw: float | None = None
    battery_soc: float | None = None
    grid_kw: float | None = None
    # Other fields omitted to keep history payloads compact.
    # If needed for specific dashboards, add per-route field filtering later.


class BuildingHistory(BaseModel):
    """Time-series response wrapper.

    The list-of-objects shape (instead of separate parallel arrays) is
    Chart.js-friendly: pass the array as `data` and configure datasets
    to read specific fields.
    """

    building_id: uuid.UUID
    start: datetime
    end: datetime
    interval_seconds: int = Field(
        ..., description="Aggregation window size used by the server.",
    )
    points: list[BuildingHistoryPoint]
