"""initial schema — organizations, sites, buildings, users, building_assignments, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-05-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── Enum types ──────────────────────────────────────────────────
    # We pre-create the enum types so that referring tables can use them
    # without create_type=True surprises during downgrade.
    organization_type = postgresql.ENUM(
        "sparki", "site_owner", "private",
        name="organization_type",
        create_type=False,
    )
    user_role = postgresql.ENUM(
        "sparki_staff", "site_owner", "tenant",
        name="user_role",
        create_type=False,
    )
    audit_action = postgresql.ENUM(
        "view", "list", "create", "update", "delete", "login", "logout", "export",
        name="audit_action",
        create_type=False,
    )
    audit_status = postgresql.ENUM(
        "allowed", "denied", "error",
        name="audit_status",
        create_type=False,
    )

    organization_type.create(op.get_bind(), checkfirst=True)
    user_role.create(op.get_bind(), checkfirst=True)
    audit_action.create(op.get_bind(), checkfirst=True)
    audit_status.create(op.get_bind(), checkfirst=True)

    # ─── 1. organizations ────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("type", organization_type, nullable=False),
        sa.Column("sigen_account_email", sa.String(255), nullable=True),
        sa.Column("sigen_credentials_encrypted", sa.LargeBinary, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_organizations_name", "organizations", ["name"])

    # ─── 2. sites ────────────────────────────────────────────────────
    op.create_table(
        "sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Brussels"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_sites_organization_id", "sites", ["organization_id"])

    # ─── 3. buildings ────────────────────────────────────────────────
    op.create_table(
        "buildings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sigen_station_id", sa.String(100), nullable=True, unique=True),
        sa.Column("installed_kwp", sa.Float, nullable=True),
        sa.Column("battery_kwh", sa.Float, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_buildings_site_id", "buildings", ["site_id"])
    op.create_index("ix_buildings_sigen_station_id", "buildings", ["sigen_station_id"])

    # ─── 4. users ────────────────────────────────────────────────────
    op.create_table(
        "users",
        # PK = Keycloak's sub UUID; not auto-generated here.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_role", "users", ["role"])

    # ─── 5. building_assignments ─────────────────────────────────────
    op.create_table(
        "building_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "building_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("buildings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("building_id", "user_id", name="uq_assignment_building_user"),
    )
    op.create_index(
        "ix_building_assignments_building_id", "building_assignments", ["building_id"],
    )
    op.create_index(
        "ix_building_assignments_user_id", "building_assignments", ["user_id"],
    )

    # ─── 6. audit_log ────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # No FK on user_id — preserve audit history after user deletion.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("status", audit_status, nullable=False),
        sa.Column("ip", postgresql.INET, nullable=True),
        sa.Column("detail", sa.String(1000), nullable=True),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_user_timestamp", "audit_log", ["user_id", "timestamp"])
    op.create_index("ix_audit_log_status_timestamp", "audit_log", ["status", "timestamp"])


def downgrade() -> None:
    # Drop tables in reverse FK order
    op.drop_table("audit_log")
    op.drop_table("building_assignments")
    op.drop_table("users")
    op.drop_table("buildings")
    op.drop_table("sites")
    op.drop_table("organizations")

    # Drop enum types
    sa.Enum(name="audit_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="audit_action").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="organization_type").drop(op.get_bind(), checkfirst=True)
