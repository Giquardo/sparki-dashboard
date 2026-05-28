"""
Integration tests for the portfolio page + live tile (Step 3.3).

Run inside the webapp container against the live stack — no mocks.

Covered:
  - Anonymous GET / serves the landing splash (not the portfolio)
  - Staff portfolio lists all 10 buildings
  - Tenant portfolio lists exactly 1 building (Woning 1)
  - Each building card embeds an hx-get to its tile route
  - The tile fragment renders for a building the user can see
  - The tile route returns 403 for a building the user CANNOT see
  - The tile route requires a session (401 when anonymous)
"""

from __future__ import annotations

import os
import re

import httpx
import pytest

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")

pytestmark = pytest.mark.asyncio


# ─── Helpers ─────────────────────────────────────────────────────────
async def _session_client(as_role: str) -> httpx.AsyncClient:
    """Return a client carrying a dev-login session cookie for `as_role`."""
    client = httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    )
    r = await client.get(f"/dev/login?as_={as_role}")
    assert r.status_code == 303
    assert "sparki_session" in r.cookies
    return client


def _building_ids_from_portfolio(html: str) -> list[str]:
    """Extract building UUIDs from the hx-get tile URLs in the portfolio."""
    return re.findall(r"/buildings/([0-9a-f-]{36})/tile", html)


# ─── Anonymous ───────────────────────────────────────────────────────
async def test_anonymous_root_is_landing_not_portfolio() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/")
    assert r.status_code == 200
    # Landing has the Inloggen CTA; portfolio has building cards.
    assert "Inloggen" in r.text
    assert "/tile" not in r.text          # no tiles on the anon landing


# ─── Portfolio per role ──────────────────────────────────────────────
async def test_staff_portfolio_lists_all_ten() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/")
        assert r.status_code == 200
        ids = _building_ids_from_portfolio(r.text)
        assert len(ids) == 10, f"Expected 10 building tiles, got {len(ids)}"
        # Card content sanity: building names present
        assert "Woning 1" in r.text
        assert "Woning 10" in r.text
    finally:
        await c.aclose()


async def test_tenant_portfolio_lists_one() -> None:
    c = await _session_client("tenant")
    try:
        r = await c.get("/")
        assert r.status_code == 200
        ids = _building_ids_from_portfolio(r.text)
        assert len(ids) == 1, f"Tenant should see exactly 1 building, got {len(ids)}"
        assert "Woning 1" in r.text
    finally:
        await c.aclose()


async def test_portfolio_cards_have_htmx_tile_triggers() -> None:
    """Each card must lazy-load its tile via HTMX with a 30s refresh."""
    c = await _session_client("staff")
    try:
        r = await c.get("/")
        body = r.text
        assert 'hx-get="/buildings/' in body
        assert "every 30s" in body
        assert 'hx-trigger="load' in body
    finally:
        await c.aclose()


# ─── Tile fragment ───────────────────────────────────────────────────
async def test_tile_renders_for_visible_building() -> None:
    c = await _session_client("staff")
    try:
        # Discover a real building id from the portfolio
        portfolio = await c.get("/")
        ids = _building_ids_from_portfolio(portfolio.text)
        assert ids, "No building tiles found in staff portfolio"
        bid = ids[0]

        tile = await c.get(f"/buildings/{bid}/tile")
        assert tile.status_code == 200
        # Fragment is partial HTML — it should NOT be a full page
        assert "<html" not in tile.text.lower()
        # Either live metrics (PV/Batterij/Net) or the no-data note
        assert ("PV" in tile.text) or ("Geen recente data" in tile.text)
    finally:
        await c.aclose()


async def test_tile_403_for_building_outside_visibility() -> None:
    """A tenant requesting a tile for a building they can't see → 403.

    We discover a building the tenant cannot see by listing the full set
    as staff, then subtracting the tenant's one visible building.
    """
    staff = await _session_client("staff")
    tenant = await _session_client("tenant")
    try:
        staff_portfolio = await staff.get("/")
        all_ids = set(_building_ids_from_portfolio(staff_portfolio.text))

        tenant_portfolio = await tenant.get("/")
        tenant_ids = set(_building_ids_from_portfolio(tenant_portfolio.text))

        forbidden = (all_ids - tenant_ids).pop()

        r = await tenant.get(f"/buildings/{forbidden}/tile")
        assert r.status_code == 403
    finally:
        await staff.aclose()
        await tenant.aclose()


async def test_tile_requires_session() -> None:
    """Anonymous request to a tile route → 401 (get_session_user_required)."""
    # Use any plausible UUID; auth check fires before visibility.
    fake = "00000000-0000-0000-0000-000000000000"
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(f"/buildings/{fake}/tile")
    assert r.status_code == 401
