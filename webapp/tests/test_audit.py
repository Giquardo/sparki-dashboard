"""Tests that audit_log rows are persisted when access is denied."""

from __future__ import annotations

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Make sure all model modules are imported so SQLAlchemy can resolve
# string-based relationships like 'Organization'.
from app.buildings.models import Building, BuildingAssignment  # noqa: F401
from app.core.audit import AuditLog, AuditStatus
from app.database import AsyncSessionLocal
from app.organizations.models import Organization  # noqa: F401
from app.sites.models import Site  # noqa: F401
from app.users.models import User  # noqa: F401


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """A direct DB session for inspecting audit_log without going through the API.

    NOTE: we DON'T use `async with` here. The session/engine pool is
    a singleton owned by the webapp module; pytest-asyncio creates a
    new event loop per test, and explicit close() on a different loop
    triggers asyncpg's "got Future attached to a different loop" error.
    Letting Python garbage-collect the session avoids that path.
    """
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        # Best-effort: close on the same loop that opened it. If that
        # fails (rare), we let the GC reclaim — the connection returns
        # to the pool either way.
        try:
            await session.close()
        except RuntimeError:
            pass


async def _count_denied(db: AsyncSession) -> int:
    stmt = select(func.count()).select_from(AuditLog).where(
        AuditLog.status == AuditStatus.DENIED,
    )
    return (await db.execute(stmt)).scalar_one()


async def test_denied_request_writes_audit_row(
    client: httpx.AsyncClient,
    tenant_headers: dict[str, str],
    forbidden_building_id: str,
    db_session: AsyncSession,
) -> None:
    """When a tenant requests a forbidden building, exactly one
    'denied' row should be added to audit_log."""
    before = await _count_denied(db_session)

    r = await client.get(
        f"/api/buildings/{forbidden_building_id}/current",
        headers=tenant_headers,
    )
    assert r.status_code == 403

    after = await _count_denied(db_session)
    assert after == before + 1, (
        f"Expected exactly 1 new denied row, got {after - before}"
    )


async def test_denied_audit_row_has_correct_fields(
    client: httpx.AsyncClient,
    tenant_headers: dict[str, str],
    forbidden_building_id: str,
    db_session: AsyncSession,
) -> None:
    """The most recent denied row should have status=denied,
    resource_type='building', and the requested resource_id."""
    await client.get(
        f"/api/buildings/{forbidden_building_id}/current",
        headers=tenant_headers,
    )

    stmt = (
        select(AuditLog)
        .where(AuditLog.status == AuditStatus.DENIED)
        .order_by(AuditLog.timestamp.desc())
        .limit(1)
    )
    last = (await db_session.execute(stmt)).scalar_one()
    assert last.resource_type == "building"
    assert last.resource_id == forbidden_building_id
    assert last.user_id is not None


async def test_allowed_request_does_not_write_audit_row(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    first_building_id: str,
    db_session: AsyncSession,
) -> None:
    """Successful (allowed) accesses should NOT show up in audit_log.
    Allowed access is logged to stdout only — by design."""
    before_allowed = (await db_session.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.status == AuditStatus.ALLOWED,
        )
    )).scalar_one()

    r = await client.get(
        f"/api/buildings/{first_building_id}/current",
        headers=staff_headers,
    )
    assert r.status_code == 200

    after_allowed = (await db_session.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.status == AuditStatus.ALLOWED,
        )
    )).scalar_one()
    assert after_allowed == before_allowed, (
        "Allowed requests should NOT create audit_log rows"
    )
