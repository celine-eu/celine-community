"""Remove the legacy anti-gaming contract-test alert.

Revision ID: 003
Revises: 002
Create Date: 2026-08-06
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_ID = "29b66c7f-8c46-4fb4-942f-8b8c2cf72911"


def upgrade() -> None:
    audit_events = sa.table("audit_events", sa.column("resource_id", sa.String()))
    manager_alerts = sa.table("manager_alerts", sa.column("id", sa.Uuid()))
    op.execute(audit_events.delete().where(audit_events.c.resource_id == _ALERT_ID))
    op.execute(manager_alerts.delete().where(manager_alerts.c.id == UUID(_ALERT_ID)))


def downgrade() -> None:
    # Contract-test records are deliberately not recreated.
    pass
