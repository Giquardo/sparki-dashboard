"""Tests for the authentication endpoints."""

from __future__ import annotations

import httpx
import pytest


# ─── /api/me — happy paths ───────────────────────────────────────────
async def test_staff_me_returns_correct_role(
    client: httpx.AsyncClient, staff_headers: dict[str, str],
) -> None:
    r = await client.get("/api/me", headers=staff_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "staff@sparki.test"
    assert body["role"] == "sparki_staff"
    assert body["is_sparki_staff"] is True
    assert body["is_site_owner"] is False
    assert body["is_tenant"] is False


async def test_owner_me_returns_correct_role(
    client: httpx.AsyncClient, owner_headers: dict[str, str],
) -> None:
    r = await client.get("/api/me", headers=owner_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "owner@sigenburg.test"
    assert body["role"] == "site_owner"
    assert body["is_site_owner"] is True


async def test_tenant_me_returns_correct_role(
    client: httpx.AsyncClient, tenant_headers: dict[str, str],
) -> None:
    r = await client.get("/api/me", headers=tenant_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "tenant@sigenburg.test"
    assert body["role"] == "tenant"
    assert body["is_tenant"] is True


# ─── /api/me — auth failures ─────────────────────────────────────────
async def test_me_without_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_me_with_invalid_token_returns_401(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get(
        "/api/me",
        headers={"Authorization": "Bearer notatoken"},
    )
    assert r.status_code == 401


async def test_me_with_malformed_auth_header_returns_401(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get(
        "/api/me",
        headers={"Authorization": "garbage"},
    )
    assert r.status_code == 401


# ─── /api/me/roles ───────────────────────────────────────────────────
async def test_roles_endpoint_returns_role_list(
    client: httpx.AsyncClient, tenant_headers: dict[str, str],
) -> None:
    r = await client.get("/api/me/roles", headers=tenant_headers)
    assert r.status_code == 200
    body = r.json()
    # Endpoint returns a list of {label, value} objects (one per role)
    assert isinstance(body, list)
    values = {item["value"] for item in body}
    assert "tenant" in values
