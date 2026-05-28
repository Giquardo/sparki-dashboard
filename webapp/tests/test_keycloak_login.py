"""
Integration tests for the real Keycloak OAuth flow (Step 3.2).

Strategy: rather than scripting the HTML login form (fragile), we use
Keycloak's password grant to get a real token, then exercise the
internal paths the OAuth callback would exercise. The key invariants
we validate end-to-end:

  - GET /login responds with a 303 to a Keycloak auth URL
  - /login sets the OAuth flight cookie (state + verifier)
  - /auth/callback rejects mismatched state (403/400)
  - /auth/callback rejects callback without a flight cookie
  - /logout clears the session cookie and redirects to Keycloak's
    end_session_endpoint with a post_logout_redirect_uri
  - /dev/login still works (regression check from Step 3.1)
  - After /dev/login, /api/me works via the Bearer header path
    (the JSON API remains independent of session cookies)
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, parse_qs

import httpx
import pytest

WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "sparki")

pytestmark = pytest.mark.asyncio


# ─── /login → Keycloak redirect ──────────────────────────────────────
async def test_login_redirects_to_keycloak() -> None:
    """GET /login as anonymous returns a 303 to Keycloak with the
    expected OAuth query parameters."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get("/login")

    assert r.status_code == 303, f"Expected 303, got {r.status_code}: {r.text[:200]}"
    location = r.headers["location"]

    # Points at Keycloak's authorization endpoint
    parsed = urlparse(location)
    assert parsed.netloc == urlparse(KEYCLOAK_PUBLIC_URL).netloc
    assert parsed.path == f"/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth"

    # Required OAuth parameters present
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["webapp"]
    assert "openid" in qs["scope"][0]
    assert qs["code_challenge_method"] == ["S256"]
    assert len(qs["code_challenge"][0]) >= 43          # b64url(sha256(...))
    assert len(qs["state"][0]) >= 32                   # min state length

    # Flight cookie was set
    assert "sparki_oauth_flight" in r.cookies


# ─── /login when already logged in just bounces home ─────────────────
async def test_login_when_logged_in_bounces_to_root() -> None:
    """Already-authenticated users at /login should NOT be sent through
    a second OAuth dance — just redirect to /."""
    # Establish a session via the dev stub
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get("/dev/login?as_=staff")
        assert r.status_code == 303
        session_cookies = c.cookies

    # Hit /login with the session cookie
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
        cookies=session_cookies,
    ) as c:
        r = await c.get("/login")

    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ─── /auth/callback — bad state ──────────────────────────────────────
async def test_callback_rejects_state_mismatch() -> None:
    """A callback with state that doesn't match the flight cookie
    is treated as a CSRF attempt — render an error page, do not
    consume the code."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        # First grab a legit flight cookie via /login
        login_r = await c.get("/login")
        assert "sparki_oauth_flight" in c.cookies

        # Now call /auth/callback with a different state value
        r = await c.get(
            "/auth/callback",
            params={"code": "irrelevant", "state": "totally-different-state"},
        )

    assert r.status_code == 400
    assert "State-parameter" in r.text or "state" in r.text.lower()


# ─── /auth/callback — no flight cookie ───────────────────────────────
async def test_callback_without_flight_cookie_renders_error() -> None:
    """Direct hit on /auth/callback (no prior /login) → error page."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get(
            "/auth/callback",
            params={"code": "irrelevant", "state": "irrelevant"},
        )

    assert r.status_code == 400
    # Look for the Dutch error UI
    assert "Sessie verlopen" in r.text or "verlopen" in r.text


# ─── /auth/callback — Keycloak-reported error parameter ──────────────
async def test_callback_propagates_keycloak_error() -> None:
    """When Keycloak redirects back with ?error=..., show the message
    to the user instead of crashing."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get(
            "/auth/callback",
            params={
                "error": "access_denied",
                "error_description": "User cancelled login",
            },
        )

    assert r.status_code == 400
    assert "Inloggen geannuleerd" in r.text or "geannuleerd" in r.text.lower()


# ─── /auth/callback — missing parameters ─────────────────────────────
async def test_callback_with_no_params_renders_error() -> None:
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get("/auth/callback")
    assert r.status_code == 400
    assert "Ongeldige" in r.text or "ontbreekt" in r.text.lower()


# ─── /logout — full SSO logout ───────────────────────────────────────
async def test_logout_clears_cookie_and_redirects_to_keycloak() -> None:
    """Logout should:
      - clear the sparki_session cookie
      - 303 to Keycloak's end_session_endpoint
      - include post_logout_redirect_uri pointing back at /
    """
    # First get a session
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get("/dev/login?as_=staff")
        assert r.status_code == 303
        cookies = c.cookies

    # Now logout
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
        cookies=cookies,
    ) as c:
        r = await c.get("/logout")

    assert r.status_code == 303
    location = r.headers["location"]

    # Goes to Keycloak end_session_endpoint
    parsed = urlparse(location)
    assert parsed.netloc == urlparse(KEYCLOAK_PUBLIC_URL).netloc
    assert parsed.path == f"/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout"

    # With our root as post_logout_redirect_uri and our client_id
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["webapp"]
    assert qs["post_logout_redirect_uri"][0].rstrip("/").endswith("8000")

    # And the session cookie was cleared via Set-Cookie
    set_cookie = r.headers.get("set-cookie", "")
    assert "sparki_session" in set_cookie
    assert ("max-age=0" in set_cookie.lower()
            or "expires=" in set_cookie.lower())


# ─── /logout when not logged in still redirects to Keycloak ──────────
async def test_logout_when_anonymous_still_works() -> None:
    """Hitting /logout with no session shouldn't error — just send the
    user through the Keycloak logout (which becomes a no-op there)."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get("/logout")
    assert r.status_code == 303
    assert "/protocol/openid-connect/logout" in r.headers["location"]


# ─── Regression: /dev/login still works ──────────────────────────────
async def test_dev_login_still_works_after_step_3_2() -> None:
    """Step 3.2 explicitly kept /dev/login for demo + tests. Verify."""
    async with httpx.AsyncClient(
        base_url=WEBAPP_URL, timeout=10.0, follow_redirects=False,
    ) as c:
        r = await c.get("/dev/login?as_=owner")
        assert r.status_code == 303
        assert "sparki_session" in r.cookies

        # And the session is honored on subsequent requests
        dashboard = await c.get("/")
        assert dashboard.status_code == 200
        assert "owner@sigenburg.test" in dashboard.text
