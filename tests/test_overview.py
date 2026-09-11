"""Overview aggregation tests for cross-service population data."""

from celine.community.services.overview import OverviewProvider


class PopulationCommunities:
    async def fetch_values(self, *, fetcher_id: str, **kwargs):
        items = (
            [{"monitored_members": 41, "monitored_devices": 41}]
            if fetcher_id == "rec_population_summary"
            else []
        )
        return type("Result", (), {"items": items})()


class PopulationDT:
    communities = PopulationCommunities()


class RegistryPage:
    items = [object()] * 44
    next_cursor = None


class RegistryResponse:
    status_code = 200
    parsed = RegistryPage()


class Community:
    key = "gr-renewable-community"
    name = "Greenland Renewable Energy Community"


class CommunityResponse:
    status_code = 200
    parsed = Community()


class PopulationRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def list_members(
        self,
        community_key: str,
        *,
        status: str | None = None,
        **kwargs,
    ):
        self.calls.append((community_key, status))
        return RegistryResponse()

    async def get_community(self, community_key: str, **kwargs):
        return CommunityResponse()


async def test_registry_fills_administrative_population_without_exposing_members() -> None:
    """The community key is the registry key — there is no translation left.

    `registry_community_key` used to carry a second name for the same REC because
    the fixture's alias was one the realm did not have. Asserting the call is made
    with the key it was asked about is what replaces it.
    """
    registry = PopulationRegistry()

    overview = await OverviewProvider().get(
        community_key="gr-renewable-community",
        period="7d",
        dt=PopulationDT(),
        registry=registry,
    )

    assert registry.calls == [("gr-renewable-community", "active")]
    assert overview.community_name == "Greenland Renewable Energy Community"
    assert overview.population.administrative_members == 44
    assert overview.population.monitored_members == 41
    assert overview.population.monitored_devices == 41
    assert overview.population.unregistered_meters == 3
    assert "rec_registry_population" not in overview.missing_sources
