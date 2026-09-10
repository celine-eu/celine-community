"""Device and data-flow endpoints for operational REC monitoring."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from celine.community.api.deps import DevicesReadDep, DTDep
from celine.community.api.schemas import (
    DataFlowResponse,
    DeviceDetailResponse,
    DeviceListResponse,
    DeviceSort,
    DeviceStatus,
    EngagementState,
    MeterGapsResponse,
    Period,
    SortOrder,
)
from celine.community.services.cache import aggregate_cache
from celine.community.services.operations import OperationalProvider

router = APIRouter(prefix="/api/communities/{community_key}", tags=["operations"])
provider = OperationalProvider()


@router.get("/devices", response_model=DeviceListResponse)
async def devices(
    community_key: str,
    user: DevicesReadDep,
    dt: DTDep,
    period: Period = "30d",
    search: str | None = None,
    status: DeviceStatus | None = None,
    engagement: EngagementState | None = None,
    sort: DeviceSort = "gap_minutes",
    order: SortOrder = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
) -> DeviceListResponse:
    return await provider.list_devices(
        community_key=community_key,
        period=period,
        dt=dt,
        search=search,
        status=status,
        engagement=engagement,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )


@router.get("/devices/{device_id}", response_model=DeviceDetailResponse)
async def device_detail(
    community_key: str,
    device_id: str,
    user: DevicesReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> DeviceDetailResponse:
    response = await provider.device_detail(
        community_key=community_key,
        device_id=device_id,
        period=period,
        dt=dt,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Device not found in this REC")
    return response


@router.get("/meters/{device_id}/gaps", response_model=MeterGapsResponse)
async def meter_gaps(
    community_key: str,
    device_id: str,
    user: DevicesReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> MeterGapsResponse:
    response = await provider.meter_gaps(
        community_key=community_key,
        device_id=device_id,
        period=period,
        dt=dt,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Device not found in this REC")
    return response


@router.get("/data-flow/pipelines", response_model=DataFlowResponse)
async def data_flow(
    community_key: str,
    user: DevicesReadDep,
    dt: DTDep,
    period: Period = "7d",
) -> DataFlowResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("data-flow", community_key, period),
        lambda: provider.data_flow(community_key=community_key, period=period, dt=dt),
    )
    return value
