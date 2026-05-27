"""
Jinja2 environment for the HTML UI.

Provides a single ``templates`` instance that routes import and use:

    from app.web.templates_env import templates

    @router.get("/")
    async def home(request: Request):
        return templates.TemplateResponse(
            request, "pages/portfolio.html", {"buildings": [...]}
        )

Custom filters we expose to templates:
  - ``nl_number``    : 1234.5  → "1.234,5"     (Dutch thousands/decimal)
  - ``nl_kw``        : 3.42    → "3,4 kW"       (energy formatting)
  - ``nl_eur_mwh``   : 87.50   → "87,50 €/MWh"
  - ``role_label``   : enum    → "Sparki staff" / "Site owner" / "Bewoner"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import settings
from app.users.models import UserRole

# ─── Locate the templates folder ─────────────────────────────────────
# webapp/templates/ — sibling of the app/ package, NOT inside it.
# Resolved relative to this file so it works regardless of CWD.
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ─── Custom filters ──────────────────────────────────────────────────
def _nl_number(value: float | int | None, decimals: int = 1) -> str:
    """Format a number Dutch-style: thousands with '.', decimals with ','.

    Example: 1234.56 → "1.234,6" at decimals=1
    None and NaN render as a thin dash.
    """
    if value is None:
        return "–"
    try:
        formatted = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "–"
    # Convert en_US grouping (1,234.56) → Dutch (1.234,56)
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _nl_kw(value: float | int | None) -> str:
    """Format a kW value: '3,4 kW' (one decimal)."""
    if value is None:
        return "– kW"
    return f"{_nl_number(value, 1)} kW"


def _nl_eur_mwh(value: float | int | None) -> str:
    """Format a price as '87,50 €/MWh' (two decimals)."""
    if value is None:
        return "– €/MWh"
    return f"{_nl_number(value, 2)} €/MWh"


def _role_label(role: UserRole | str | None) -> str:
    """Map a UserRole to a Dutch label suitable for the UI."""
    if role is None:
        return "Onbekend"
    value = role.value if isinstance(role, UserRole) else str(role)
    mapping = {
        "sparki_staff": "Sparki medewerker",
        "site_owner": "Site-eigenaar",
        "tenant": "Bewoner",
    }
    return mapping.get(value, value)


templates.env.filters["nl_number"] = _nl_number
templates.env.filters["nl_kw"] = _nl_kw
templates.env.filters["nl_eur_mwh"] = _nl_eur_mwh
templates.env.filters["role_label"] = _role_label


# ─── Global template context ─────────────────────────────────────────
# Anything in here is available to every template without being passed
# explicitly in the route — used for things that NEVER change per request.
templates.env.globals.update(
    app_version=__version__,
    app_environment=settings.environment,
    is_production=settings.is_production,
)


def template_context(
    request: Any,                       # fastapi.Request, kept loose to avoid import cycle
    **extra: Any,
) -> dict[str, Any]:
    """Build a context dict with the common keys every page needs.

    Always includes:
      - ``request``          (required by Jinja2Templates)
      - ``current_path``     (handy for active-link highlighting)
      - any per-route extras passed as kwargs

    The ``user`` key is NOT injected here — routes pass it explicitly via
    the ``get_session_user_optional`` / ``_required`` dependencies so
    static analysis can flag missing-user pages.
    """
    return {
        "request": request,
        "current_path": request.url.path,
        **extra,
    }
