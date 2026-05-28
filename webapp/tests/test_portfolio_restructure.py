"""
Integration tests for the Portfolio/Gebouwen restructure (Step 3.6).

  / (Portfolio)  → per-site summary cards
  /buildings     → building card grid
  /sites/{id}/live.json → aggregate live PV for a site

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
    return client


def _site_ids(html: str) -> list[str]:
    return re.findall(r"/sites/([0-9a-f-]{36})/live\.json", html)


def _building_ids(html: str) -> list[str]:
    return re.findall(r"/buildings/([0-9a-f-]{36})/tile", html)


# ─── Portfolio (/) — per-site summary ────────────────────────────────
async def test_portfolio_shows_site_summary_for_staff() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/")
        assert r.status_code == 200
        body = r.text
        # Seed data: 1 site "Wijk Sint-Jan" with 10 buildings
        assert "Wijk Sint-Jan" in body
        # Summary stats labels present
        assert "PV capaciteit" in body
        assert "Batterij" in body
        assert "PV nu" in body
        # Per-site live aggregate route is wired
        site_ids = _site_ids(body)
        assert len(site_ids) >= 1
        # It's the summary, NOT the card grid → no per-building tiles here
        assert "/tile" not in body or "/tile/full" not in body
    finally:
        await c.aclose()


async def test_portfolio_tenant_sees_their_site() -> None:
    c = await _session_client("tenant")
    try:
        r = await c.get("/")
        assert r.status_code == 200
        # Tenant's one building is in Wijk Sint-Jan
        assert "Wijk Sint-Jan" in r.text
        # Exactly one site summary card
        assert len(_site_ids(r.text)) == 1
    finally:
        await c.aclose()


async def test_portfolio_anonymous_is_landing() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "Inloggen" in r.text
    assert "PV capaciteit" not in r.text       # no summary for anon


# ─── Gebouwen (/buildings) — card grid ───────────────────────────────
async def test_buildings_grid_staff_all_ten() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/buildings")
        assert r.status_code == 200
        ids = _building_ids(r.text)
        assert len(ids) == 10
        assert "Woning 1" in r.text and "Woning 10" in r.text
    finally:
        await c.aclose()


async def test_buildings_grid_tenant_one() -> None:
    c = await _session_client("tenant")
    try:
        r = await c.get("/buildings")
        assert r.status_code == 200
        assert len(_building_ids(r.text)) == 1
    finally:
        await c.aclose()


async def test_buildings_requires_session() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/buildings")
    assert r.status_code == 401


# ─── Site live aggregate ─────────────────────────────────────────────
async def test_site_live_json_shape() -> None:
    c = await _session_client("staff")
    try:
        portfolio = await c.get("/")
        sids = _site_ids(portfolio.text)
        assert sids
        r = await c.get(f"/sites/{sids[0]}/live.json")
        assert r.status_code == 200
        body = r.json()
        assert "total_pv_kw" in body
        assert "buildings" in body
        assert "has_data" in body
        assert body["buildings"] >= 1
    finally:
        await c.aclose()


async def test_site_live_json_403_for_unseen_site() -> None:
    """A tenant requesting live data for a site where they have no
    visible buildings gets 403. We use a random site UUID (the tenant
    has no buildings there)."""
    c = await _session_client("tenant")
    try:
        fake_site = "00000000-0000-0000-0000-000000000000"
        r = await c.get(f"/sites/{fake_site}/live.json")
        assert r.status_code == 403
    finally:
        await c.aclose()


async def test_site_live_json_requires_session() -> None:
    fake_site = "00000000-0000-0000-0000-000000000000"
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(f"/sites/{fake_site}/live.json")
    assert r.status_code == 401
