"""Gamification and read-only nudging routes."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from celine.community.api.deps import (
    AlertsWriteDep,
    DbDep,
    DTDep,
    GamificationReadDep,
    NudgingDep,
    NudgingReadDep,
)
from celine.community.api.schemas import (
    AntiGamingFlag,
    AntiGamingFlagsResponse,
    NudgingConversionResponse,
    Period,
    PointsDistributionResponse,
    PointsLedgerResponse,
)
from celine.community.db.models import AlertAck, AuditEvent, ManagerAlert
from celine.community.services.cache import aggregate_cache
from celine.community.services.engagement import GamificationProvider, NudgingProvider

router = APIRouter(prefix="/api/communities/{community_key}", tags=["engagement"])
gamification = GamificationProvider()
nudging = NudgingProvider()


@router.get("/points/distribution", response_model=PointsDistributionResponse)
async def points_distribution(
    community_key: str,
    user: GamificationReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> PointsDistributionResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("points-distribution", community_key, period),
        lambda: gamification.distribution(community_key, period, dt),
    )
    return value


async def _acknowledged_flag_ids(db: DbDep, user_id: str) -> set:
    result = await db.execute(select(AlertAck.alert_id).where(AlertAck.user_id == user_id))
    return set(result.scalars().all())


@router.get("/points/flags", response_model=AntiGamingFlagsResponse)
async def points_flags(
    community_key: str,
    user: GamificationReadDep,
    dt: DTDep,
    db: DbDep,
    period: Period = "30d",
) -> AntiGamingFlagsResponse:
    response, _ = await aggregate_cache.get_or_set(
        ("points-flags", community_key, period),
        lambda: gamification.flags(community_key, period, dt),
    )
    acknowledged = await _acknowledged_flag_ids(db, user.sub)
    return response.model_copy(
        update={
            "items": [
                item.model_copy(
                    update={"state": "acknowledged" if item.id in acknowledged else "open"}
                )
                for item in response.items
            ]
        }
    )


@router.post("/points/flags/{flag_id}/ack", response_model=AntiGamingFlag)
async def acknowledge_points_flag(
    community_key: str,
    flag_id: str,
    user: AlertsWriteDep,
    dt: DTDep,
    db: DbDep,
    period: Period = "30d",
) -> AntiGamingFlag:
    try:
        wanted_id = UUID(flag_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Anti-gaming flag not found in this REC"
        ) from exc
    response = await gamification.flags(community_key, period, dt)
    flag = next((item for item in response.items if item.id == wanted_id), None)
    if flag is None:
        raise HTTPException(status_code=404, detail="Anti-gaming flag not found in this REC")

    alert = await db.get(ManagerAlert, flag.id)
    if alert is None:
        alert = ManagerAlert(
            id=flag.id,
            community_key=community_key,
            source="anti-gaming",
            severity=flag.severity,
            title=f"Anti-gaming: {flag.rule}",
            detail=flag.detail,
            resource_type="device",
            resource_id=flag.device_id,
        )
        db.add(alert)
        await db.flush()
    existing = await db.scalar(
        select(AlertAck).where(AlertAck.alert_id == flag.id).where(AlertAck.user_id == user.sub)
    )
    if existing is None:
        db.add(AlertAck(alert_id=flag.id, user_id=user.sub))
        db.add(
            AuditEvent(
                community_key=community_key,
                actor_id=user.sub,
                action="community.points.flag.acknowledge",
                resource_type="anti_gaming_flag",
                resource_id=str(flag.id),
                detail={"device_id": flag.device_id, "rule": flag.rule},
            )
        )
        await db.commit()
    return flag.model_copy(update={"state": "acknowledged"})


@router.get("/devices/{device_id}/points/ledger", response_model=PointsLedgerResponse)
async def points_ledger(
    community_key: str,
    device_id: str,
    user: GamificationReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> PointsLedgerResponse:
    response = await gamification.ledger(community_key, device_id, period, dt)
    if response is None:
        raise HTTPException(status_code=404, detail="Points ledger not found in this REC")
    return response


@router.get("/nudging/conversion", response_model=NudgingConversionResponse)
async def nudging_conversion(
    community_key: str,
    user: NudgingReadDep,
    nudging_client: NudgingDep,
    period: Period = "30d",
) -> NudgingConversionResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("nudging-conversion", community_key, period),
        lambda: nudging.conversion(community_key, period, nudging_client),
    )
    return value
