"""rename buildings.sigen_station_id to sigen_system_id

Sigencloud's official API names the installation identifier `systemId`,
not `stationId`. We rename the column + the matching index for clarity
and so Node-RED queries match Sigencloud's vocabulary.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename the column itself
    op.alter_column(
        "buildings",
        "sigen_station_id",
        new_column_name="sigen_system_id",
    )
    # Rename the index that backs the column (created in migration 0001)
    op.execute(
        "ALTER INDEX ix_buildings_sigen_station_id "
        "RENAME TO ix_buildings_sigen_system_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_buildings_sigen_system_id "
        "RENAME TO ix_buildings_sigen_station_id"
    )
    op.alter_column(
        "buildings",
        "sigen_system_id",
        new_column_name="sigen_station_id",
    )
