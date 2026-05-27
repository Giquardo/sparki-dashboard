"""
Pydantic API schemas for the prices domain.

We expose prices in EUR/MWh (ENTSO-E's native unit) AND EUR/kWh
(more user-friendly) so the UI doesn't need to do conversion.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field


class PricePoint(BaseModel):
    """One hourly day-ahead price."""

    timestamp: datetime = Field(..., description="Start of the hourly interval (UTC)")
    eur_per_mwh: float = Field(..., description="Price in EUR/MWh (native ENTSO-E unit)")

    @computed_field
    def eur_per_kwh(self) -> float:
        """Convenience: price per kWh (eur_per_mwh / 1000)."""
        return round(self.eur_per_mwh / 1000, 6)


class PriceSeries(BaseModel):
    """A series of consecutive hourly prices for one bidding zone."""

    zone: str = Field(..., description="Bidding zone code (e.g. 'BE', 'NL', 'DE-LU')")
    start: datetime
    end: datetime
    source: str = Field(
        ...,
        description="Data source: 'entsoe' for live, 'mock' for fallback",
    )
    points: list[PricePoint]
