"""Manager-owned community objective routes."""

from fastapi import APIRouter
from sqlalchemy import select

from celine.community.api.deps import CommunityReadDep, DbDep, ObjectivesWriteDep
from celine.community.api.schemas import (
    ObjectiveProgress,
    ObjectivesResponse,
    ObjectivesUpdate,
    ObjectiveValue,
)
from celine.community.db.models import AuditEvent, CommunityObjective

router = APIRouter(prefix="/api/communities/{community_key}/objectives", tags=["objectives"])


async def _stored(community_key: str, period: str, db: DbDep) -> list[CommunityObjective]:
    result = await db.execute(
        select(CommunityObjective)
        .where(CommunityObjective.community_key == community_key)
        .where(CommunityObjective.period == period)
        .order_by(CommunityObjective.objective_id)
    )
    return list(result.scalars().all())


@router.get("", response_model=ObjectivesResponse)
async def get_objectives(
    community_key: str,
    user: CommunityReadDep,
    db: DbDep,
    period: str = "monthly",
) -> ObjectivesResponse:
    rows = await _stored(community_key, period, db)
    objectives = [
        ObjectiveValue(id=row.objective_id, target=row.target, unit=row.unit) for row in rows
    ]
    return ObjectivesResponse(community_key=community_key, period=period, objectives=objectives)


@router.put("", response_model=ObjectivesResponse)
async def put_objectives(
    community_key: str,
    body: ObjectivesUpdate,
    user: ObjectivesWriteDep,
    db: DbDep,
) -> ObjectivesResponse:
    existing = {row.objective_id: row for row in await _stored(community_key, body.period, db)}
    for item in body.objectives:
        row = existing.get(item.id)
        if row:
            row.target = item.target
            row.unit = item.unit
            row.updated_by = user.sub
        else:
            db.add(
                CommunityObjective(
                    community_key=community_key,
                    period=body.period,
                    objective_id=item.id,
                    target=item.target,
                    unit=item.unit,
                    updated_by=user.sub,
                )
            )
    db.add(
        AuditEvent(
            community_key=community_key,
            actor_id=user.sub,
            action="community.objectives.update",
            resource_type="community_objectives",
            resource_id=body.period,
            detail={"objective_ids": [item.id for item in body.objectives]},
        )
    )
    await db.commit()
    return ObjectivesResponse(
        community_key=community_key,
        period=body.period,
        objectives=body.objectives,
    )


@router.get("/progress", response_model=list[ObjectiveProgress])
async def objectives_progress(
    community_key: str,
    user: CommunityReadDep,
) -> list[ObjectiveProgress]:
    # Targets are manager-owned; progress is emitted only when a governed current-value
    # integration exists. Returning no rows is preferable to manufacturing a zero value.
    return []
