"""Remove the retired development alert records.

Revision ID: 002
Revises: 001
Create Date: 2026-08-06
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_IDS = (
    "0c6025ca-8d58-4d8d-a4d5-7112dd05c1c0",
    "99eef500-d792-4764-8ec8-72280b21b9ae",
    "bb8223e6-ddd0-4b7c-a790-d45276162d3e",
)


def upgrade() -> None:
    audit_events = sa.table("audit_events", sa.column("resource_id", sa.String()))
    manager_alerts = sa.table("manager_alerts", sa.column("id", sa.Uuid()))
    op.execute(audit_events.delete().where(audit_events.c.resource_id.in_(_ALERT_IDS)))
    op.execute(manager_alerts.delete().where(manager_alerts.c.id.in_(map(UUID, _ALERT_IDS))))


def downgrade() -> None:
    # Synthetic records are deliberately not recreated.
    pass
