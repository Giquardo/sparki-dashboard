"""Tests for GET /api/buildings — list endpoint with visibility filter."""

from __future__ import annotations

import httpx


# ─── Per-role visibility ─────────────────────────────────────────────
async def test_staff_sees_all_ten_buildings(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/buildings", headers=staff_headers)
    assert r.status_code == 200
    buildings = r.json()
    assert len(buildings) == 10
    # Demo seed creates Woning 1 .. Woning 10
    names = {b["name"] for b in buildings}
    assert "Woning 1" in names
    assert "Woning 10" in names


async def test_owner_sees_all_ten_buildings_of_org(
    client: httpx.AsyncClient, owner_headers: dict[str, str],
) -> None:
    r = await client.get("/api/buildings", headers=owner_headers)
    assert r.status_code == 200
    buildings = r.json()
    # The single demo customer org has all 10 buildings
    assert len(buildings) == 10


async def test_tenant_sees_only_assigned_building(
    client: httpx.AsyncClient, tenant_headers: dict[str, str],
) -> None:
    r = await client.get("/api/buildings", headers=tenant_headers)
    assert r.status_code == 200
    buildings = r.json()
    assert len(buildings) == 1
    assert buildings[0]["name"] == "Woning 1"


# ─── Auth required ───────────────────────────────────────────────────
async def test_list_without_token_returns_401(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/buildings")
    assert r.status_code == 401


# ─── Response shape ──────────────────────────────────────────────────
async def test_list_response_has_expected_fields(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/buildings", headers=staff_headers)
    assert r.status_code == 200
    first = r.json()[0]
    for key in ("id", "name", "site_id", "sigen_system_id",
                "installed_kwp", "battery_kwh", "active"):
        assert key in first, f"Missing field {key!r} in BuildingOut"


async def test_buildings_sorted_by_name(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/buildings", headers=staff_headers)
    names = [b["name"] for b in r.json()]
    assert names == sorted(names), "Buildings should be sorted alphabetically"
