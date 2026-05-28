"""
Jinja2 environment for the HTML UI.

Provides a single ``templates`` instance that routes import and use:

    from app.web.templates_env import templates

Custom filters exposed to templates:
  - ``nl_number``    : 1234.5  → "1.234,5"     (Dutch thousands/decimal)
  - ``nl_kw``        : 3.42    → "3,4 kW"       (energy formatting)
  - ``nl_eur_mwh``   : 87.50   → "87,50 €/MWh"
  - ``role_label``   : enum    → "Sparki medewerker" / ...
  - ``nl_time``      : UTC dt  → "13:57:30"     (Europe/Brussels local time)
  - ``nl_datetime``  : UTC dt  → "28/05 13:57"  (Brussels local, with date)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import settings
from app.users.models import UserRole

# ─── Locate the templates folder ─────────────────────────────────────
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Display timezone for the UI. Belgium = Europe/Brussels (CET/CEST,
# DST handled automatically by zoneinfo). InfluxDB stores UTC; we
# convert at render time so users see local wall-clock time.
_DISPLAY_TZ = ZoneInfo("Europe/Brussels")


# ─── Number filters ──────────────────────────────────────────────────
def _nl_number(value: float | int | None, decimals: int = 1) -> str:
    if value is None:
        return "–"
    try:
        formatted = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "–"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _nl_kw(value: float | int | None) -> str:
    if value is None:
        return "– kW"
    return f"{_nl_number(value, 1)} kW"


def _nl_eur_mwh(value: float | int | None) -> str:
    if value is None:
        return "– €/MWh"
    return f"{_nl_number(value, 2)} €/MWh"


def _role_label(role: UserRole | str | None) -> str:
    if role is None:
        return "Onbekend"
    value = role.value if isinstance(role, UserRole) else str(role)
    mapping = {
        "sparki_staff": "Sparki medewerker",
        "site_owner": "Site-eigenaar",
        "tenant": "Bewoner",
    }
    return mapping.get(value, value)


# ─── Time filters ────────────────────────────────────────────────────
def _to_brussels(dt: datetime) -> datetime:
    """Convert a datetime to Europe/Brussels.

    Naive datetimes are ASSUMED to be UTC (InfluxDB always stores UTC),
    then converted. Aware datetimes are converted directly.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DISPLAY_TZ)


def _nl_time(dt: datetime | None) -> str:
    """Format a UTC datetime as Brussels local time: '13:57:30'."""
    if dt is None:
        return "–"
    return _to_brussels(dt).strftime("%H:%M:%S")


def _nl_datetime(dt: datetime | None) -> str:
    """Format a UTC datetime as Brussels local date+time: '28/05 13:57'."""
    if dt is None:
        return "–"
    return _to_brussels(dt).strftime("%d/%m %H:%M")


templates.env.filters["nl_number"] = _nl_number
templates.env.filters["nl_kw"] = _nl_kw
templates.env.filters["nl_eur_mwh"] = _nl_eur_mwh
templates.env.filters["role_label"] = _role_label
templates.env.filters["nl_time"] = _nl_time
templates.env.filters["nl_datetime"] = _nl_datetime


# ─── Global template context ─────────────────────────────────────────
templates.env.globals.update(
    app_version=__version__,
    app_environment=settings.environment,
    is_production=settings.is_production,
)


def template_context(request: Any, **extra: Any) -> dict[str, Any]:
    """Build a context dict with the common keys every page needs."""
    return {
        "request": request,
        "current_path": request.url.path,
        **extra,
    }
