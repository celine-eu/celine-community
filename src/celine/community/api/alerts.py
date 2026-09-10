"""Manager alert inbox and audited workflow actions."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from celine.community.api.deps import AlertsReadDep, AlertsWriteDep, DbDep
from celine.community.api.schemas import (
    AlertAssignRequest,
    AlertMuteRequest,
    AlertsResponse,
    AlertState,
    AuditEventResponse,
    ManagerAlertResponse,
    Severity,
)
from celine.community.db.models import AlertAck, AuditEvent, ManagerAlert

router = APIRouter(prefix="/api/communities/{community_key}/alerts", tags=["alerts"])
ROME = ZoneInfo("Europe/Rome")


def _now() -> datetime:
    return datetime.now(ROME)


def _state(alert: ManagerAlert, acknowledged: bool) -> AlertState:
    if alert.muted_until and alert.muted_until > _now():
        return "muted"
    return "acknowledged" if acknowledged else "open"


def _response(alert: ManagerAlert, acknowledged: bool) -> ManagerAlertResponse:
    return ManagerAlertResponse(
        id=alert.id,
        source=alert.source,
        severity=alert.severity,
        title=alert.title,
        detail=alert.detail,
        resource_type=alert.resource_type,
        resource_id=alert.resource_id,
        assigned_to=alert.assigned_to,
        muted_until=alert.muted_until,
        active=alert.active,
        acknowledged=acknowledged,
        state=_state(alert, acknowledged),
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


async def _alert(community_key: str, alert_id: UUID, db: DbDep) -> ManagerAlert:
    alert = await db.scalar(
        select(ManagerAlert)
        .where(ManagerAlert.id == alert_id)
        .where(ManagerAlert.community_key == community_key)
        .where(ManagerAlert.active.is_(True))
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found in this REC")
    return alert


async def _is_acknowledged(alert_id: UUID, user_id: str, db: DbDep) -> bool:
    return (
        await db.scalar(
            select(AlertAck.id)
            .where(AlertAck.alert_id == alert_id)
            .where(AlertAck.user_id == user_id)
        )
        is not None
    )


def _audit(
    community_key: str,
    actor_id: str,
    action: str,
    alert_id: UUID,
    detail: dict | None = None,
) -> AuditEvent:
    return AuditEvent(
        community_key=community_key,
        actor_id=actor_id,
        action=action,
        resource_type="manager_alert",
        resource_id=str(alert_id),
        detail=detail or {},
    )


@router.get("", response_model=AlertsResponse)
async def alerts(
    community_key: str,
    user: AlertsReadDep,
    db: DbDep,
    severity: Severity | None = None,
    state: AlertState | None = None,
    source: str | None = None,
) -> AlertsResponse:
    result = await db.execute(
        select(ManagerAlert)
        .where(ManagerAlert.community_key == community_key)
        .where(ManagerAlert.active.is_(True))
        .order_by(ManagerAlert.created_at.desc())
    )
    rows = list(result.scalars().all())
    ack_result = await db.execute(select(AlertAck.alert_id).where(AlertAck.user_id == user.sub))
    acknowledged = set(ack_result.scalars().all())
    items = [_response(row, row.id in acknowledged) for row in rows]
    if severity:
        items = [item for item in items if item.severity == severity]
    if state:
        items = [item for item in items if item.state == state]
    if source:
        items = [item for item in items if item.source == source]
    return AlertsResponse(community_key=community_key, total=len(items), items=items)


@router.get("/audit-events", response_model=list[AuditEventResponse])
async def audit_events(
    community_key: str,
    user: AlertsReadDep,
    db: DbDep,
) -> list[AuditEventResponse]:
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.community_key == community_key)
        .order_by(AuditEvent.created_at.desc())
        .limit(100)
    )
    return [
        AuditEventResponse.model_validate(item, from_attributes=True) for item in result.scalars()
    ]


@router.post("/{alert_id}/ack", response_model=ManagerAlertResponse)
async def acknowledge_alert(
    community_key: str,
    alert_id: UUID,
    user: AlertsWriteDep,
    db: DbDep,
) -> ManagerAlertResponse:
    alert = await _alert(community_key, alert_id, db)
    acknowledged = await _is_acknowledged(alert_id, user.sub, db)
    if not acknowledged:
        db.add(AlertAck(alert_id=alert_id, user_id=user.sub))
        db.add(_audit(community_key, user.sub, "community.alert.acknowledge", alert_id))
        await db.commit()
    return _response(alert, True)


@router.post("/{alert_id}/mute", response_model=ManagerAlertResponse)
async def mute_alert(
    community_key: str,
    alert_id: UUID,
    body: AlertMuteRequest,
    user: AlertsWriteDep,
    db: DbDep,
) -> ManagerAlertResponse:
    if body.muted_until <= _now():
        raise HTTPException(status_code=422, detail="mutedUntil must be in the future")
    alert = await _alert(community_key, alert_id, db)
    alert.muted_until = body.muted_until
    db.add(
        _audit(
            community_key,
            user.sub,
            "community.alert.mute",
            alert_id,
            {"muted_until": body.muted_until.isoformat()},
        )
    )
    await db.commit()
    await db.refresh(alert)
    return _response(alert, await _is_acknowledged(alert_id, user.sub, db))


@router.post("/{alert_id}/assign", response_model=ManagerAlertResponse)
async def assign_alert(
    community_key: str,
    alert_id: UUID,
    body: AlertAssignRequest,
    user: AlertsWriteDep,
    db: DbDep,
) -> ManagerAlertResponse:
    alert = await _alert(community_key, alert_id, db)
    alert.assigned_to = body.assigned_to
    db.add(
        _audit(
            community_key,
            user.sub,
            "community.alert.assign",
            alert_id,
            {"assigned_to": body.assigned_to},
        )
    )
    await db.commit()
    await db.refresh(alert)
    return _response(alert, await _is_acknowledged(alert_id, user.sub, db))
