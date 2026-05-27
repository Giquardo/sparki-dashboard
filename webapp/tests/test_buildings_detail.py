"""Tests for GET /api/buildings/{id}/current and /history."""

from __future__ import annotations

import httpx

from tests.conftest import FAKE_UUID


# ─── /current — happy paths ──────────────────────────────────────────
async def test_staff_can_get_current_for_any_building(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/current",
        headers=staff_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["building_id"] == first_building_id
    # We expect ALL ten fields in the schema, even if some are None
    for field in (
        "pv_kw", "load_kw", "ev_charger_kw", "heatpump_kw",
        "battery_kw", "battery_soc",
        "grid_kw", "export_kw", "import_kw", "self_consumption_kw",
    ):
        assert field in body


async def test_tenant_can_get_current_for_own_building(
    client: httpx.AsyncClient,
    tenant_headers: dict[str, str],
    tenant_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{tenant_building_id}/current",
        headers=tenant_headers,
    )
    assert r.status_code == 200


# ─── /current — permission denied ────────────────────────────────────
async def test_tenant_gets_403_on_other_buildings_current(
    client: httpx.AsyncClient,
    tenant_headers: dict[str, str],
    forbidden_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{forbidden_building_id}/current",
        headers=tenant_headers,
    )
    assert r.status_code == 403


async def test_tenant_gets_403_on_random_uuid(
    client: httpx.AsyncClient,
    tenant_headers: dict[str, str],
) -> None:
    """A random non-existent UUID should give 403 (not 404) for tenants —
    we don't leak which buildings exist by returning different status codes."""
    r = await client.get(
        f"/api/buildings/{FAKE_UUID}/current",
        headers=tenant_headers,
    )
    assert r.status_code == 403


async def test_random_uuid_returns_403_for_all_roles(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
) -> None:
    """A random non-existent UUID returns 403 — not 404 — because our
    permission check fires before the DB existence lookup, and an
    unknown UUID is by definition not in any user's visible set.

    This is intentional: a 404 would leak which UUIDs exist."""
    r = await client.get(
        f"/api/buildings/{FAKE_UUID}/current",
        headers=staff_headers,
    )
    assert r.status_code == 403


# ─── /history — happy paths ──────────────────────────────────────────
async def test_staff_can_get_history(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/history",
        headers=staff_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["building_id"] == first_building_id
    assert "points" in body
    assert "start" in body
    assert "end" in body
    assert "interval_seconds" in body
    assert body["interval_seconds"] == 60  # default


async def test_history_with_custom_interval(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/history?interval_seconds=300",
        headers=staff_headers,
    )
    assert r.status_code == 200
    assert r.json()["interval_seconds"] == 300


# ─── /history — validation errors ────────────────────────────────────
async def test_history_rejects_too_small_interval(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/history?interval_seconds=5",
        headers=staff_headers,
    )
    assert r.status_code == 422  # FastAPI validation error


async def test_history_rejects_too_large_interval(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/history?interval_seconds=4000",
        headers=staff_headers,
    )
    assert r.status_code == 422


async def test_history_rejects_end_before_start(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/history"
        "?start=2026-01-01T00:00:00Z&end=2025-01-01T00:00:00Z",
        headers=staff_headers,
    )
    assert r.status_code == 400
    assert "after" in r.json()["detail"].lower()


async def test_history_rejects_range_over_30_days(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{first_building_id}/history"
        "?start=2025-01-01T00:00:00Z&end=2026-05-26T00:00:00Z",
        headers=staff_headers,
    )
    assert r.status_code == 400
    assert "30-day" in r.json()["detail"]


# ─── /history — permission ───────────────────────────────────────────
async def test_tenant_gets_403_on_other_buildings_history(
    client: httpx.AsyncClient,
    tenant_headers: dict[str, str],
    forbidden_building_id: str,
) -> None:
    r = await client.get(
        f"/api/buildings/{forbidden_building_id}/history",
        headers=tenant_headers,
    )
    assert r.status_code == 403
