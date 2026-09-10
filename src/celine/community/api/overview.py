"""REC overview endpoint."""

from fastapi import APIRouter

from celine.community.api.deps import CommunityReadDep, DTDep, RegistryDep
from celine.community.api.schemas import OverviewResponse, Period
from celine.community.services.cache import aggregate_cache
from celine.community.services.overview import OverviewProvider
from celine.community.settings import settings

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
    name = (
        settings.dev_community_name
        if settings.dev_auth_enabled and community_key == settings.dev_community_key
        else community_key.replace("-", " ").title()
    )
    value, _ = await aggregate_cache.get_or_set(
        ("overview", community_key, period),
        lambda: provider.get(
            community_key=community_key,
            community_name=name,
            period=period,
            dt=dt,
            registry=registry,
            registry_community_key=settings.rec_registry_community_key,
        ),
    )
    return value
