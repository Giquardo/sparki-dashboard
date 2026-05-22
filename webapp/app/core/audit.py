"""
AuditLog model.

Every data access and every permission violation is logged here. This is
the GDPR audit trail referenced in the project plan: who looked at what,
when, from which IP, and whether it was allowed.

Design notes:
  - PK is BIGINT auto-increment, not UUID. This table will be the largest
    in the DB by far, and integer PKs are smaller + faster.
  - No FK constraint on `user_id`: we want to keep audit rows even if the
    user is later deleted (GDPR right-to-erasure shouldn't erase audit history).
  - `resource_id` is a STRING (not UUID), because we audit access to many
    different resource types — buildings, sites, organizations, settings.
  - Indexed on (user_id, timestamp DESC) for fast "what did this user do" queries.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Enum, Index, String
from sqlalchemy.dialects.postgresql import INET, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class AuditAction(str, enum.Enum):
    """What the user was attempting to do."""

    VIEW = "view"             # read access to a resource
    LIST = "list"             # list/index of resources
    CREATE = "create"         # insert
    UPDATE = "update"         # modify
    DELETE = "delete"         # remove
    LOGIN = "login"           # successful login
    LOGOUT = "logout"
    EXPORT = "export"         # download / data export (future)


class AuditStatus(str, enum.Enum):
    """Outcome of the action."""

    ALLOWED = "allowed"       # access granted, action performed
    DENIED = "denied"         # 403 — permission check failed
    ERROR = "error"           # exception during processing


class AuditLog(Base):
    """One row per data-access attempt.

    Note: does NOT inherit from TimestampMixin — we use one explicit
    `timestamp` field instead. (`updated_at` makes no sense for an
    append-only audit log.)
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        # Frequently-asked queries: "show me this user's recent activity"
        Index("ix_audit_log_user_timestamp", "user_id", "timestamp"),
        # And: "show me recent denied attempts" (for security review)
        Index("ix_audit_log_status_timestamp", "status", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )

    # No FK on purpose — we want to keep audit rows after a user is deleted.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True,
    )

    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action"), nullable=False,
    )
    # Resource type as a free-form string: "building", "site", "organization", etc.
    # Free-form to keep audit forward-compatible without migrations.
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    status: Mapped[AuditStatus] = mapped_column(
        Enum(AuditStatus, name="audit_status"), nullable=False,
    )
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    # Optional free-form context — e.g. error message, requested filter, etc.
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditLog #{self.id} {self.action.value} {self.resource_type}/"
            f"{self.resource_id} → {self.status.value}>"
        )
