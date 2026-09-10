"""Authorized CSV/XLSX exports for manager-facing tables."""

from fastapi import APIRouter
from sqlalchemy import select

from celine.community.api.deps import (
    AlertsReadDep,
    DbDep,
    DevicesReadDep,
    DTDep,
    FlexibilityReadDep,
    GamificationReadDep,
    NudgingDep,
    NudgingReadDep,
)
from celine.community.api.schemas import Period
from celine.community.db.models import ManagerAlert
from celine.community.services.engagement import GamificationProvider, NudgingProvider
from celine.community.services.export import ExportFormat, tabular_response
from celine.community.services.flexibility import FlexibilityProvider
from celine.community.services.operations import OperationalProvider

router = APIRouter(prefix="/api/communities/{community_key}/exports", tags=["exports"])
operations = OperationalProvider()
flexibility = FlexibilityProvider()
gamification = GamificationProvider()
nudging = NudgingProvider()


@router.get("/devices")
async def export_devices(
    community_key: str,
    user: DevicesReadDep,
    dt: DTDep,
    period: Period = "30d",
    format: ExportFormat = "csv",
):
    devices, _ = await operations.export_devices(community_key, period, dt)
    return tabular_response(
        filename=f"celine-{community_key}-devices-{period}",
        format=format,
        columns=[
            ("device_id", "Device ID"),
            ("meter_status", "Meter status"),
            ("engagement_state", "Engagement"),
            ("last_seen", "Last seen"),
            ("gap_minutes", "Gap minutes"),
            ("coverage_percent", "Coverage %"),
            ("points_30d", "Points 30d"),
        ],
        rows=[item.model_dump(mode="json") for item in devices],
    )


@router.get("/flexibility")
async def export_flexibility(
    community_key: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
    format: ExportFormat = "csv",
):
    response = await flexibility.windows(community_key, period, dt)
    return tabular_response(
        filename=f"celine-{community_key}-flexibility-{period}",
        format=format,
        columns=[
            ("id", "Window ID"),
            ("start", "Start"),
            ("end", "End"),
            ("state", "State"),
            ("offered_kwh", "Offered kWh"),
            ("committed_kwh", "Committed kWh"),
            ("delivered_kwh", "Delivered kWh"),
            ("delivery_rate", "Delivery %"),
            ("correlation_state", "Correlation"),
        ],
        rows=[item.model_dump(mode="json") for item in response.items],
    )


@router.get("/points")
async def export_points(
    community_key: str,
    user: GamificationReadDep,
    dt: DTDep,
    period: Period = "30d",
    format: ExportFormat = "csv",
):
    response = await gamification.distribution(community_key, period, dt)
    return tabular_response(
        filename=f"celine-{community_key}-points-{period}",
        format=format,
        columns=[
            ("rank", "Rank"),
            ("device_id", "Device ID"),
            ("points", "Points"),
            ("trend", "Rank trend"),
        ],
        rows=[item.model_dump(mode="json") for item in response.leaderboard],
    )


@router.get("/nudging")
async def export_nudging(
    community_key: str,
    user: NudgingReadDep,
    nudging_client: NudgingDep,
    period: Period = "30d",
    format: ExportFormat = "csv",
):
    response = await nudging.conversion(community_key, period, nudging_client)
    rows = []
    for rule in response.rules:
        counts = {step.id: step.count for step in rule.steps}
        rows.append({**rule.model_dump(mode="json"), **counts})
    return tabular_response(
        filename=f"celine-{community_key}-nudging-{period}",
        format=format,
        columns=[
            ("id", "Rule ID"),
            ("name", "Rule"),
            ("family", "Family"),
            ("channel", "Channel"),
            ("active", "Active"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("clicked", "Clicked"),
            ("committed", "Committed"),
        ],
        rows=rows,
    )


@router.get("/alerts")
async def export_alerts(
    community_key: str,
    user: AlertsReadDep,
    db: DbDep,
    format: ExportFormat = "csv",
):
    result = await db.execute(
        select(ManagerAlert)
        .where(ManagerAlert.community_key == community_key)
        .order_by(ManagerAlert.created_at.desc())
    )
    return tabular_response(
        filename=f"celine-{community_key}-alerts",
        format=format,
        columns=[
            ("id", "Alert ID"),
            ("source", "Source"),
            ("severity", "Severity"),
            ("title", "Title"),
            ("resource_type", "Resource type"),
            ("resource_id", "Resource ID"),
            ("assigned_to", "Assigned to"),
            ("muted_until", "Muted until"),
            ("active", "Active"),
            ("created_at", "Created at"),
        ],
        rows=[
            {
                "id": str(item.id),
                "source": item.source,
                "severity": item.severity,
                "title": item.title,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "assigned_to": item.assigned_to,
                "muted_until": item.muted_until.isoformat() if item.muted_until else None,
                "active": item.active,
                "created_at": item.created_at.isoformat(),
            }
            for item in result.scalars()
        ],
    )
