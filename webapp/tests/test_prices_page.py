"""
Integration tests for the Prijzen (prices) page + current-price route (3.5).

Run inside the webapp container against the live stack — no mocks.
"""

from __future__ import annotations

import os

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
    return client


# ─── Page ────────────────────────────────────────────────────────────
async def test_prices_page_renders_for_any_role() -> None:
    """Prices are market-wide — even a tenant can open the page."""
    c = await _session_client("tenant")
    try:
        r = await c.get("/prices")
        assert r.status_code == 200
        body = r.text
        assert "<html" in body.lower()
        assert 'id="priceChart"' in body
        assert "Huidige prijs" in body
        # Chart pulls the series + current routes client-side
        assert "/prices/BE.json" in body or "${ZONE}.json" in body
        assert "current.json" in body
    finally:
        await c.aclose()


async def test_prices_page_requires_session() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/prices")
    assert r.status_code == 401


# ─── current.json ────────────────────────────────────────────────────
async def test_current_json_shape() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/prices/BE/current.json")
        assert r.status_code == 200
        body = r.json()
        assert "available" in body
        assert body["zone"] == "BE"
        if body["available"]:
            assert "eur_per_mwh" in body
            assert "eur_per_kwh" in body
            assert "timestamp" in body
    finally:
        await c.aclose()


async def test_current_json_unknown_zone_400() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/prices/FR/current.json")
        assert r.status_code == 400
    finally:
        await c.aclose()


async def test_current_json_requires_session() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/prices/BE/current.json")
    assert r.status_code == 401


# ─── Coexistence with the series route from Step 3.4 ─────────────────
async def test_series_and_current_routes_coexist() -> None:
    """The Step 3.4 series route (/prices/{zone}.json) and the new
    current route (/prices/{zone}/current.json) must not shadow each
    other — both should resolve correctly."""
    c = await _session_client("staff")
    try:
        series = await c.get("/prices/BE.json")
        current = await c.get("/prices/BE/current.json")
        assert series.status_code == 200
        assert current.status_code == 200
        assert "points" in series.json()        # series shape
        assert "available" in current.json()     # current shape
    finally:
        await c.aclose()
