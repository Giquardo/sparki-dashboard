"""Tests for the system / health endpoints."""

from __future__ import annotations

import httpx


async def test_root_returns_200(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200


async def test_healthz_is_public(client: httpx.AsyncClient) -> None:
    """Liveness probe — no auth required, always available."""
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_readyz_returns_check_results(client: httpx.AsyncClient) -> None:
    """Readiness probe checks DB + InfluxDB. Should be 200 in healthy stack."""
    r = await client.get("/readyz")
    # If anything is down we get 503, otherwise 200
    assert r.status_code in (200, 503)
    body = r.json()
    assert "checks" in body
    # `checks` is a dict {service_name: result_dict}.
    assert "postgres" in body["checks"]
    assert "influxdb" in body["checks"]
