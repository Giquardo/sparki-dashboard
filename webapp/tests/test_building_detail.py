"""
Integration tests for the building detail page + chart data routes (3.4).

UPDATED in Step 3.6: building-ID discovery uses /buildings (the card grid)
instead of "/" — "/" is now the per-site Portfolio summary.

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


def _building_ids(html: str) -> list[str]:
    return re.findall(r"/buildings/([0-9a-f-]{36})/tile", html)


async def _first_visible_id(client: httpx.AsyncClient) -> str:
    grid = await client.get("/buildings")          # card grid, not "/"
    ids = _building_ids(grid.text)
    assert ids, "no visible buildings for this user"
    return ids[0]


# ─── Detail page ─────────────────────────────────────────────────────
async def test_detail_page_renders_for_visible_building() -> None:
    c = await _session_client("staff")
    try:
        bid = await _first_visible_id(c)
        r = await c.get(f"/buildings/{bid}")
        assert r.status_code == 200
        body = r.text
        assert "<html" in body.lower()
        assert 'id="energyChart"' in body
        assert 'id="socChart"' in body
        assert "/tile/full" in body
        assert "Terug naar portfolio" in body
    finally:
        await c.aclose()


async def test_detail_page_403_for_forbidden_building() -> None:
    staff = await _session_client("staff")
    tenant = await _session_client("tenant")
    try:
        all_ids = set(_building_ids((await staff.get("/buildings")).text))
        tenant_ids = set(_building_ids((await tenant.get("/buildings")).text))
        forbidden = (all_ids - tenant_ids).pop()
        r = await tenant.get(f"/buildings/{forbidden}")
        assert r.status_code == 403
    finally:
        await staff.aclose()
        await tenant.aclose()


async def test_detail_page_401_when_anonymous() -> None:
    fake = "00000000-0000-0000-0000-000000000000"
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(f"/buildings/{fake}")
    assert r.status_code == 401


# ─── Full tile fragment ──────────────────────────────────────────────
async def test_full_tile_renders() -> None:
    c = await _session_client("staff")
    try:
        bid = await _first_visible_id(c)
        r = await c.get(f"/buildings/{bid}/tile/full")
        assert r.status_code == 200
        assert "<html" not in r.text.lower()
        assert ("Productie" in r.text) or ("Geen recente data" in r.text)
    finally:
        await c.aclose()


# ─── history.json ────────────────────────────────────────────────────
async def test_history_json_shape() -> None:
    c = await _session_client("staff")
    try:
        bid = await _first_visible_id(c)
        r = await c.get(f"/buildings/{bid}/history.json")
        assert r.status_code == 200
        body = r.json()
        assert body["building_id"] == bid
        assert "points" in body and isinstance(body["points"], list)
        assert "interval_seconds" in body
        if body["points"]:
            p = body["points"][0]
            for field in ("timestamp", "pv_kw", "load_kw",
                          "battery_soc", "grid_kw"):
                assert field in p
    finally:
        await c.aclose()


async def test_history_json_403_for_forbidden_building() -> None:
    staff = await _session_client("staff")
    tenant = await _session_client("tenant")
    try:
        all_ids = set(_building_ids((await staff.get("/buildings")).text))
        tenant_ids = set(_building_ids((await tenant.get("/buildings")).text))
        forbidden = (all_ids - tenant_ids).pop()
        r = await tenant.get(f"/buildings/{forbidden}/history.json")
        assert r.status_code == 403
    finally:
        await staff.aclose()
        await tenant.aclose()


async def test_history_json_requires_session() -> None:
    fake = "00000000-0000-0000-0000-000000000000"
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(f"/buildings/{fake}/history.json")
    assert r.status_code == 401


# ─── prices.json ─────────────────────────────────────────────────────
async def test_prices_json_returns_series() -> None:
    c = await _session_client("tenant")
    try:
        r = await c.get("/prices/BE.json")
        assert r.status_code == 200
        body = r.json()
        assert body["zone"] == "BE"
        assert "points" in body
        assert body["source"] in ("entsoe", "mock", "unknown")
    finally:
        await c.aclose()


async def test_prices_json_unknown_zone_400() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/prices/FR.json")
        assert r.status_code == 400
    finally:
        await c.aclose()


async def test_prices_json_requires_session() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/prices/BE.json")
    assert r.status_code == 401
