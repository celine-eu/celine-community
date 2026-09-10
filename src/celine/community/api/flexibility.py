"""Flexibility and end-to-end demonstration endpoints."""

from fastapi import APIRouter, HTTPException

from celine.community.api.deps import DTDep, FlexibilityReadDep
from celine.community.api.schemas import (
    DemonstrationChainResponse,
    DemonstrationReachResponse,
    DemonstrationSummaryResponse,
    FlexibilityUptakeResponse,
    FlexibilityWindowDetailResponse,
    FlexibilityWindowsResponse,
    Period,
)
from celine.community.services.cache import aggregate_cache
from celine.community.services.flexibility import FlexibilityProvider

router = APIRouter(prefix="/api/communities/{community_key}", tags=["flexibility"])
provider = FlexibilityProvider()


@router.get("/flexibility/windows", response_model=FlexibilityWindowsResponse)
async def flexibility_windows(
    community_key: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> FlexibilityWindowsResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("flexibility-windows", community_key, period),
        lambda: provider.windows(community_key, period, dt),
    )
    return value


@router.get("/flexibility/uptake", response_model=FlexibilityUptakeResponse)
async def flexibility_uptake(
    community_key: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> FlexibilityUptakeResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("flexibility-uptake", community_key, period),
        lambda: provider.uptake(community_key, period, dt),
    )
    return value


async def _window_detail(
    community_key: str,
    window_id: str,
    period: Period,
    dt: DTDep,
) -> FlexibilityWindowDetailResponse:
    response = await provider.detail(community_key, window_id, period, dt)
    if response is None:
        raise HTTPException(status_code=404, detail="Flexibility window not found in this REC")
    return response


@router.get(
    "/flexibility/windows/{window_id}",
    response_model=FlexibilityWindowDetailResponse,
)
async def flexibility_window_detail(
    community_key: str,
    window_id: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> FlexibilityWindowDetailResponse:
    return await _window_detail(community_key, window_id, period, dt)


@router.get("/demonstration/chain", response_model=DemonstrationChainResponse)
async def demonstration_chain(
    community_key: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> DemonstrationChainResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("demonstration-chain", community_key, period),
        lambda: provider.chain(community_key, period, dt),
    )
    return value


@router.get(
    "/demonstration/windows/{window_id}",
    response_model=FlexibilityWindowDetailResponse,
)
async def demonstration_window_detail(
    community_key: str,
    window_id: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> FlexibilityWindowDetailResponse:
    return await _window_detail(community_key, window_id, period, dt)


@router.get("/demonstration/reach", response_model=DemonstrationReachResponse)
async def demonstration_reach(
    community_key: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> DemonstrationReachResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("demonstration-reach", community_key, period),
        lambda: provider.reach(community_key, period, dt),
    )
    return value


@router.get("/demonstration/summary", response_model=DemonstrationSummaryResponse)
async def demonstration_summary(
    community_key: str,
    user: FlexibilityReadDep,
    dt: DTDep,
    period: Period = "30d",
) -> DemonstrationSummaryResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("demonstration-summary", community_key, period),
        lambda: provider.summary(community_key, period, dt),
    )
    return value
