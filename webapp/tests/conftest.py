"""
Shared pytest fixtures for the Sparki test suite.

All tests run against the LIVE webapp container — no mocks. That means:
  - The Docker stack must be up (`docker compose up -d`)
  - Seed data must exist (3 users, 10 buildings — run `scripts/seed.py`)
  - Keycloak must have the demo realm imported

Fixtures provided:
  - `client`            — httpx.AsyncClient against the webapp
  - `staff_token`       — JWT for staff@sparki.test (sparki_staff role)
  - `owner_token`       — JWT for owner@sigenburg.test (site_owner role)
  - `tenant_token`      — JWT for tenant@sigenburg.test (tenant role)
  - `staff_headers` etc. — `{"Authorization": "Bearer ..."}` dicts
  - `first_building_id` — UUID of a real building (Woning 1)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

# ─── Configuration ───────────────────────────────────────────────────
# These point to other containers via Docker's internal network. From
# the webapp container's perspective: localhost = the webapp itself,
# keycloak / postgres / influxdb = service names.
WEBAPP_URL = os.environ.get("TEST_WEBAPP_URL", "http://localhost:8000")
KEYCLOAK_URL = os.environ.get(
    "TEST_KEYCLOAK_URL", "http://keycloak:8080"
)
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "sparki")
KEYCLOAK_CLIENT = os.environ.get("KEYCLOAK_CLIENT_ID", "webapp")
KEYCLOAK_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")

DEMO_PASSWORD = "Sparki!1234"


# ─── HTTP client ─────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client bound to the webapp base URL.

    Function-scoped (default) instead of session-scoped: pytest-asyncio
    creates a new event loop per test function, so a session-wide
    httpx client would point at a dead loop after the first test.

    We yield-then-aclose manually instead of `async with`. The context
    manager triggers anyio's transport.close() which can race with
    pytest-asyncio's loop teardown — calling aclose() explicitly inside
    the still-running test loop sidesteps that.
    """
    c = httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0)
    try:
        yield c
    finally:
        try:
            await c.aclose()
        except (RuntimeError, OSError):
            # Loop already closed or transport already torn down — ignore.
            pass


# ─── Token helper ────────────────────────────────────────────────────
async def _get_token(username: str) -> str:
    """Fetch an access token from Keycloak using the password grant."""
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": KEYCLOAK_CLIENT,
        "client_secret": KEYCLOAK_SECRET,
        "username": username,
        "password": DEMO_PASSWORD,
        "scope": "openid",
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, data=data)
    if r.status_code != 200:
        raise RuntimeError(
            f"Keycloak token fetch failed for {username}: "
            f"{r.status_code} {r.text}"
        )
    return r.json()["access_token"]


# ─── Token fixtures, one per role ────────────────────────────────────
@pytest_asyncio.fixture
async def staff_token() -> str:
    return await _get_token("staff@sparki.test")


@pytest_asyncio.fixture
async def owner_token() -> str:
    return await _get_token("owner@sigenburg.test")


@pytest_asyncio.fixture
async def tenant_token() -> str:
    return await _get_token("tenant@sigenburg.test")


# ─── Headers (just sugar over tokens) ────────────────────────────────
@pytest_asyncio.fixture
async def staff_headers(staff_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {staff_token}"}


@pytest_asyncio.fixture
async def owner_headers(owner_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {owner_token}"}


@pytest_asyncio.fixture
async def tenant_headers(tenant_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant_token}"}


# ─── Building ID — dynamic discovery so we never hard-code UUIDs ─────
@pytest_asyncio.fixture
async def all_building_ids(staff_token: str) -> list[str]:
    """All building IDs (10 of them) as the staff sees them."""
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(
            "/api/buildings",
            headers={"Authorization": f"Bearer {staff_token}"},
        )
    r.raise_for_status()
    return [b["id"] for b in r.json()]


@pytest_asyncio.fixture
async def first_building_id(all_building_ids: list[str]) -> str:
    """A real building ID — the first alphabetically by name."""
    return all_building_ids[0]


@pytest_asyncio.fixture
async def tenant_building_id(tenant_token: str) -> str:
    """The single building the demo tenant is assigned to."""
    async with httpx.AsyncClient(base_url=WEBAPP_URL, timeout=10.0) as c:
        r = await c.get(
            "/api/buildings",
            headers={"Authorization": f"Bearer {tenant_token}"},
        )
    r.raise_for_status()
    buildings = r.json()
    assert len(buildings) == 1, (
        f"Demo tenant should see exactly 1 building, got {len(buildings)}"
    )
    return buildings[0]["id"]


@pytest_asyncio.fixture
async def forbidden_building_id(
    all_building_ids: list[str],
    tenant_building_id: str,
) -> str:
    """A building ID the demo tenant is NOT allowed to see."""
    for bid in all_building_ids:
        if bid != tenant_building_id:
            return bid
    raise RuntimeError("Could not find a building outside tenant's visibility")


# A fake-but-valid-format UUID — used to test 403 (not 404) on bad input.
FAKE_UUID = "00000000-0000-0000-0000-000000000001"
