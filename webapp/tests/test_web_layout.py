"""
Integration tests for the Phase 3.1 HTML UI layer.

These run inside the webapp container against the full Docker stack,
following the same pattern as the rest of tests/ — no mocks.

Covered:
  - Anonymous GET / serves the landing splash
  - Anonymous GET /login serves the login placeholder
  - /dev/login?as_=staff (etc.) sets a signed session cookie and
    redirects to /
  - With that cookie, GET / serves the dashboard placeholder
  - The user's display name + email show up on the page
  - The role-aware sidebar shows /users only to staff & owner
  - /logout clears the cookie

These tests do NOT replace the 39 existing API tests — they're additive.
"""

from __future__ import annotations

import os

import httpx
import pytest

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")

pytestmark = pytest.mark.asyncio


# ─── Helpers ─────────────────────────────────────────────────────────
async def _dev_login(as_role: str) -> httpx.AsyncClient:
    """Build an httpx client whose cookie jar holds an active dev session."""
    client = httpx.AsyncClient(
        base_url=WEBAPP_URL,
        timeout=10.0,
        follow_redirects=False,
    )
    r = await client.get(f"/dev/login?as_={as_role}")
    assert r.status_code == 303, (
        f"Expected 303 from /dev/login, got {r.status_code}: {r.text[:200]}"
    )
    assert "sparki_session" in r.cookies, "No session cookie was set"
    return client


# ─── Anonymous routes ────────────────────────────────────────────────
async def test_anonymous_root_serves_landing() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    # Brand identity present
    assert "SPARKI" in body
    # Login CTA in NL
    assert "Inloggen" in body


async def test_anonymous_login_serves_placeholder() -> None:
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get("/login")
    assert r.status_code == 200
    assert "Inloggen" in r.text


# ─── Dev stub login + dashboard render ───────────────────────────────
async def test_dev_login_staff_sets_cookie_and_renders_dashboard() -> None:
    client = await _dev_login("staff")
    try:
        r = await client.get("/")
        assert r.status_code == 200
        body = r.text
        assert "staff@sparki.test" in body
        assert "Sparki medewerker" in body         # role label, Dutch
        # Staff sees admin section in sidebar
        assert "Gebruikers" in body
        assert "Instellingen" in body
    finally:
        await client.aclose()


async def test_dev_login_owner_sees_users_but_not_settings() -> None:
    client = await _dev_login("owner")
    try:
        r = await client.get("/")
        assert r.status_code == 200
        body = r.text
        assert "owner@sigenburg.test" in body
        assert "Site-eigenaar" in body
        assert "Gebruikers" in body                 # owner can manage users
        assert "Instellingen" not in body           # staff-only
    finally:
        await client.aclose()


async def test_dev_login_tenant_sees_neither_admin_link() -> None:
    client = await _dev_login("tenant")
    try:
        r = await client.get("/")
        assert r.status_code == 200
        body = r.text
        assert "tenant@sigenburg.test" in body
        assert "Bewoner" in body
        assert "Gebruikers" not in body
        assert "Instellingen" not in body
    finally:
        await client.aclose()


# ─── Logout ──────────────────────────────────────────────────────────
async def test_logout_clears_cookie_and_lands_on_splash() -> None:
    client = await _dev_login("staff")
    try:
        client_follow = httpx.AsyncClient(
            base_url=WEBAPP_URL, timeout=10.0,
            cookies=client.cookies, follow_redirects=False,
        )
        r = await client_follow.get("/logout")
        assert r.status_code == 303
        # The Set-Cookie header should clear the session (Max-Age=0 or expired)
        set_cookie = r.headers.get("set-cookie", "")
        assert "sparki_session" in set_cookie
        # max-age=0 OR expires in the past — itsdangerous sets it via delete_cookie
        assert ("max-age=0" in set_cookie.lower()
                or "expires=" in set_cookie.lower())
        await client_follow.aclose()
    finally:
        await client.aclose()


# ─── Cookie tamper resistance ────────────────────────────────────────
async def test_tampered_session_cookie_falls_back_to_anonymous() -> None:
    """A garbage cookie value should not crash the app — the user is
    treated as anonymous and the landing splash renders."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL,
        timeout=10.0,
        cookies={"sparki_session": "not-a-real-signed-cookie"},
    ) as c:
        r = await c.get("/")
    assert r.status_code == 200
    # Landing page (anonymous) renders, not the dashboard placeholder
    assert "Hallo " not in r.text     # would be present on dashboard
    assert "Inloggen" in r.text
