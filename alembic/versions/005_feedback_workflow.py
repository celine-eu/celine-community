"""Add the manager feedback review workflow.

Revision ID: 005
Revises: 004
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feedback_entries",
        sa.Column("status", sa.String(length=16), server_default="new", nullable=False),
    )
    op.add_column(
        "feedback_entries", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "feedback_entries", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "feedback_entries", sa.Column("status_updated_by", sa.String(length=255), nullable=True)
    )
    op.create_index(
        "ix_feedback_entries_community_status",
        "feedback_entries",
        ["community_key", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_entries_community_status", table_name="feedback_entries")
    op.drop_column("feedback_entries", "status_updated_by")
    op.drop_column("feedback_entries", "resolved_at")
    op.drop_column("feedback_entries", "seen_at")
    op.drop_column("feedback_entries", "status")
