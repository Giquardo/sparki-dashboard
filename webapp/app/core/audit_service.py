"""
Audit-log helpers.

Two audiences:
  - GDPR / compliance / overheid → persistent rows in the `audit_log` table
  - Ops / debugging → structured stdout logging captured by Docker

By default we ONLY write DENIED attempts to the database. ALLOWED
access is emitted as structured INFO log lines, captured by Docker's
log driver.

Important design decision: denied writes go to their own DB session
(not the request-scoped one). Why: the request that triggered the
denial raises HTTPException → the request session rolls back → our
audit row would be lost. An independent session+commit ensures the
audit trail survives even if the request fails afterwards.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from app.core.audit import AuditAction, AuditLog, AuditStatus
from app.database import AsyncSessionLocal

if TYPE_CHECKING:
    from fastapi import Request

    from app.auth.schemas import CurrentUser

logger = logging.getLogger("sparki.audit")


# ─── Public API ──────────────────────────────────────────────────────
async def log_access_denied(
    *,
    user: "CurrentUser | None",
    action: AuditAction,
    resource_type: str,
    resource_id: str | uuid.UUID,
    request: "Request | None" = None,
    detail: str | None = None,
) -> None:
    """Persist a DENIED access attempt to audit_log + emit a warning log.

    Uses its OWN database session+commit so the row survives even if
    the surrounding request rolls back (which it will, due to the
    HTTPException raised after this call).

    Never raises — audit logging failures should not break the
    user-facing response.
    """
    ip = _extract_ip(request)

    # Always emit the structured warning, even if DB write fails.
    logger.warning(
        "AUDIT denied: user=%s role=%s action=%s resource=%s/%s ip=%s detail=%s",
        user.id if user else None,
        user.role.value if user else None,
        action.value,
        resource_type,
        resource_id,
        ip,
        detail,
    )

    await _write_audit_row(
        user_id=user.id if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        status=AuditStatus.DENIED,
        ip=ip,
        detail=detail,
    )


def log_access_allowed(
    user: "CurrentUser",
    *,
    action: AuditAction,
    resource_type: str,
    resource_id: str | uuid.UUID | None = None,
    request: "Request | None" = None,
) -> None:
    """Emit a structured INFO log for an ALLOWED access.

    By design this does NOT touch the database — see module docstring.
    """
    ip = _extract_ip(request)
    logger.info(
        "AUDIT allowed: user=%s role=%s action=%s resource=%s/%s ip=%s",
        user.id,
        user.role.value,
        action.value,
        resource_type,
        resource_id if resource_id is not None else "-",
        ip,
    )


# ─── Internals ───────────────────────────────────────────────────────
async def _write_audit_row(
    *,
    user_id: uuid.UUID | None,
    action: AuditAction,
    resource_type: str,
    resource_id: str,
    status: AuditStatus,
    ip: str | None,
    detail: str | None,
) -> None:
    """Insert one row in audit_log in an independent transaction.

    A fresh session + explicit commit makes the audit write durable
    independent of the calling request's success/failure.

    Errors are logged but never raised — audit logging must never
    break the user-facing flow.
    """
    try:
        async with AsyncSessionLocal() as session:
            row = AuditLog(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                ip=ip,
                detail=detail,
            )
            session.add(row)
            await session.commit()
    except SQLAlchemyError as exc:
        logger.error(
            "Audit row insert failed (event still emitted to log): %s",
            exc,
            exc_info=True,
        )


def _extract_ip(request: "Request | None") -> str | None:
    """Best-effort client IP extraction.

    When the app sits behind Caddy/Nginx, X-Forwarded-For carries the
    real client IP. Otherwise we fall back to the direct socket peer.
    """
    if request is None:
        return None

    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()

    if request.client:
        return request.client.host

    return None
