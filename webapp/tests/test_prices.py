"""Tests for the prices API."""

from __future__ import annotations

import httpx


# ─── /current — happy path ───────────────────────────────────────────
async def test_be_current_price_available(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/prices/BE/current", headers=staff_headers)
    # Either OK (data ingested) or 404 (no data yet). We accept both —
    # 404 is correct behavior when the Node-RED price flow hasn't run yet.
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.json()
        assert "timestamp" in body
        assert "eur_per_mwh" in body
        assert "eur_per_kwh" in body
        # Sanity: eur_per_kwh should be 1000x smaller than eur_per_mwh
        if body["eur_per_mwh"] != 0:
            ratio = body["eur_per_mwh"] / body["eur_per_kwh"]
            assert 999 < ratio < 1001, f"Bad MWh→kWh conversion: ratio={ratio}"


# ─── /series — happy path ────────────────────────────────────────────
async def test_be_price_series_returns_shape(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/prices/BE", headers=staff_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["zone"] == "BE"
    assert "start" in body and "end" in body
    assert "source" in body
    assert body["source"] in ("entsoe", "mock", "unknown")
    assert "points" in body
    assert isinstance(body["points"], list)


# ─── Zone validation ─────────────────────────────────────────────────
async def test_unknown_zone_returns_400(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/prices/FR", headers=staff_headers)
    assert r.status_code == 400
    assert "zone" in r.json()["detail"].lower()


async def test_lowercase_zone_is_normalized(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    # 'be' should be uppercased and accepted as BE
    r = await client.get("/api/prices/be", headers=staff_headers)
    assert r.status_code == 200


# ─── Range validation ────────────────────────────────────────────────
async def test_end_before_start_returns_400(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get(
        "/api/prices/BE"
        "?start=2026-01-01T00:00:00Z&end=2025-01-01T00:00:00Z",
        headers=staff_headers,
    )
    assert r.status_code == 400


async def test_range_over_30_days_returns_400(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get(
        "/api/prices/BE"
        "?start=2025-01-01T00:00:00Z&end=2026-05-26T00:00:00Z",
        headers=staff_headers,
    )
    assert r.status_code == 400


# ─── Auth ────────────────────────────────────────────────────────────
async def test_prices_require_authentication(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/prices/BE")
    assert r.status_code == 401


# ─── Tenant access — prices are NOT building-scoped ──────────────────
async def test_tenant_can_see_prices(
    client: httpx.AsyncClient, tenant_headers: dict[str, str],
) -> None:
    """Prices are market data — every authenticated user sees them,
    regardless of building visibility."""
    r = await client.get("/api/prices/BE", headers=tenant_headers)
    assert r.status_code == 200
