"""REC overview endpoint."""

from fastapi import APIRouter

from celine.community.api.deps import CommunityReadDep, DTDep, RegistryDep
from celine.community.api.schemas import OverviewResponse, Period
from celine.community.services.cache import aggregate_cache
from celine.community.services.overview import OverviewProvider

router = APIRouter(prefix="/api/communities/{community_key}", tags=["overview"])
provider = OverviewProvider()


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    community_key: str,
    user: CommunityReadDep,
    dt: DTDep,
    registry: RegistryDep,
    period: Period = "7d",
) -> OverviewResponse:
    value, _ = await aggregate_cache.get_or_set(
        ("overview", community_key, period),
        lambda: provider.get(
            community_key=community_key,
            period=period,
            dt=dt,
            registry=registry,
        ),
    )
    return value
