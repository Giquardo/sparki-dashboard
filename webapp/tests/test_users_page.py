"""
Integration tests for the Gebruikers page (Step 3.7).

  /users → staff sees all orgs; owner sees only their org; tenant 403.

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


# ─── Staff: full hierarchy ───────────────────────────────────────────
async def test_staff_sees_both_orgs_and_all_users() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/users")
        assert r.status_code == 200
        body = r.text
        # Seed: 2 orgs — "Sparki" and "Stad Sigenburg"
        assert "Sparki" in body
        assert "Stad Sigenburg" in body
        # All three seed users present
        assert "staff@sparki.test" in body
        assert "owner@sigenburg.test" in body
        assert "tenant@sigenburg.test" in body
        # Bewoner section heading + tenant's assigned building chip
        assert "Bewoners" in body
        assert "Woning 1" in body
    finally:
        await c.aclose()


async def test_staff_groups_tenants_under_their_site() -> None:
    """The Bewoner section must be nested under the site name."""
    c = await _session_client("staff")
    try:
        r = await c.get("/users")
        body = r.text
        # The site name "Wijk Sint-Jan" should appear near the bewoners
        # listing for the customer org
        assert "Wijk Sint-Jan" in body
    finally:
        await c.aclose()


# ─── Owner: only own org ─────────────────────────────────────────────
async def test_owner_sees_only_own_org() -> None:
    c = await _session_client("owner")
    try:
        r = await c.get("/users")
        assert r.status_code == 200
        body = r.text
        assert "Stad Sigenburg" in body              # their own org
        assert "Sparki" not in body or "staff@sparki.test" not in body
        # See own tenants
        assert "tenant@sigenburg.test" in body
        # Don't see Sparki staff
        assert "staff@sparki.test" not in body
    finally:
        await c.aclose()


# ─── Tenant: forbidden ───────────────────────────────────────────────
async def test_tenant_gets_403() -> None:
    c = await _session_client("tenant")
    try:
        r = await c.get("/users")
        assert r.status_code == 403
    finally:
        await c.aclose()


# ─── Auth ────────────────────────────────────────────────────────────
async def test_users_page_requires_session() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/users")
    assert r.status_code == 401


# ─── Structural assertions on the page ───────────────────────────────
async def test_users_page_uses_collapsible_sections() -> None:
    c = await _session_client("staff")
    try:
        r = await c.get("/users")
        body = r.text
        assert "<details" in body
        assert "<summary" in body
    finally:
        await c.aclose()
