"""Add manager feedback persistence.

Revision ID: 004
Revises: 003
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_key", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("page_title", sa.Text(), nullable=True),
        sa.Column("page_path", sa.Text(), nullable=True),
        sa.Column("locale", sa.String(32), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("viewport_width", sa.Integer(), nullable=True),
        sa.Column("viewport_height", sa.Integer(), nullable=True),
        sa.Column("screen_width", sa.Integer(), nullable=True),
        sa.Column("screen_height", sa.Integer(), nullable=True),
        sa.Column("color_scheme", sa.String(16), nullable=True),
        sa.Column("client_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(50), nullable=True),
        sa.Column("extra_context", sa.JSON(), nullable=True),
        sa.Column("screenshot_mime_type", sa.String(64), nullable=True),
        sa.Column("screenshot_bytes", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_entries_community_key", "feedback_entries", ["community_key"])
    op.create_index("ix_feedback_entries_user_id", "feedback_entries", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_entries_user_id", table_name="feedback_entries")
    op.drop_index("ix_feedback_entries_community_key", table_name="feedback_entries")
    op.drop_table("feedback_entries")
