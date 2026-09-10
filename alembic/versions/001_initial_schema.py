"""Initial manager dashboard state tables.

Revision ID: 001
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "community_objectives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_key", sa.String(255), nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("objective_id", sa.String(64), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("community_key", "period", "objective_id", name="uq_objective_period"),
    )
    op.create_index(
        "ix_community_objectives_community_key", "community_objectives", ["community_key"]
    )

    op.create_table(
        "manager_alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_key", sa.String(255), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("assigned_to", sa.String(255), nullable=True),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_manager_alerts_community_key", "manager_alerts", ["community_key"])

    op.create_table(
        "alert_acks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["alert_id"], ["manager_alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "user_id", name="uq_alert_ack_user"),
    )
    op.create_index("ix_alert_acks_alert_id", "alert_acks", ["alert_id"])

    op.create_table(
        "saved_views",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_key", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("route", sa.String(255), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_views_community_key", "saved_views", ["community_key"])
    op.create_index("ix_saved_views_user_id", "saved_views", ["user_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_key", sa.String(255), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_community_key", "audit_events", ["community_key"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])

    op.create_table(
        "manager_settings",
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("locale", sa.String(12), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("default_period", sa.String(12), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("manager_settings")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_index("ix_audit_events_community_key", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_saved_views_user_id", table_name="saved_views")
    op.drop_index("ix_saved_views_community_key", table_name="saved_views")
    op.drop_table("saved_views")
    op.drop_index("ix_alert_acks_alert_id", table_name="alert_acks")
    op.drop_table("alert_acks")
    op.drop_index("ix_manager_alerts_community_key", table_name="manager_alerts")
    op.drop_table("manager_alerts")
    op.drop_index("ix_community_objectives_community_key", table_name="community_objectives")
    op.drop_table("community_objectives")
