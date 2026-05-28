"""
Integration tests for the building card grid (originally Step 3.3).

UPDATED in Step 3.6: the card grid moved from "/" to "/buildings".
"/" is now the per-site Portfolio summary (see test_portfolio_restructure.py).
These tests therefore target /buildings for the grid behavior.

Run inside the webapp container against the live stack — no mocks.
"""

from __future__ import annotations

import os
import re

import httpx
import pytest

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")

pytestmark = pytest.mark.asyncio


async def _session_client(as_role: str) -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    )
    r = await client.get(f"/dev/login?as_={as_role}")
    assert r.status_code == 303
    assert "sparki_session" in r.cookies
    return client


def _building_ids_from_grid(html: str) -> list[str]:
    return re.findall(r"/buildings/([0-9a-f-]{36})/tile", html)


# ─── Anonymous ───────────────────────────────────────────────────────
async def test_anonymous_root_is_landing_not_grid() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "Inloggen" in r.text
    assert "/tile" not in r.text


# ─── Card grid now lives at /buildings ───────────────────────────────
async def test_staff_grid_lists_all_ten() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/buildings")
        assert r.status_code == 200
        ids = _building_ids_from_grid(r.text)
        assert len(ids) == 10, f"Expected 10 building tiles, got {len(ids)}"
        assert "Woning 1" in r.text
        assert "Woning 10" in r.text
    finally:
        await c.aclose()


async def test_tenant_grid_lists_one() -> None:
    c = await _session_client("tenant")
    try:
        r = await c.get("/buildings")
        assert r.status_code == 200
        ids = _building_ids_from_grid(r.text)
        assert len(ids) == 1, f"Tenant should see exactly 1 building, got {len(ids)}"
        assert "Woning 1" in r.text
    finally:
        await c.aclose()


async def test_grid_cards_have_htmx_tile_triggers() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/buildings")
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
        grid = await c.get("/buildings")
        ids = _building_ids_from_grid(grid.text)
        assert ids
        bid = ids[0]
        tile = await c.get(f"/buildings/{bid}/tile")
        assert tile.status_code == 200
        assert "<html" not in tile.text.lower()
        assert ("PV" in tile.text) or ("Geen recente data" in tile.text)
    finally:
        await c.aclose()


async def test_tile_403_for_building_outside_visibility() -> None:
    staff = await _session_client("staff")
    tenant = await _session_client("tenant")
    try:
        all_ids = set(_building_ids_from_grid((await staff.get("/buildings")).text))
        tenant_ids = set(_building_ids_from_grid((await tenant.get("/buildings")).text))
        forbidden = (all_ids - tenant_ids).pop()
        r = await tenant.get(f"/buildings/{forbidden}/tile")
        assert r.status_code == 403
    finally:
        await staff.aclose()
        await tenant.aclose()


async def test_tile_requires_session() -> None:
    fake = "00000000-0000-0000-0000-000000000000"
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(f"/buildings/{fake}/tile")
    assert r.status_code == 401
