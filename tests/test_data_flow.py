"""The data-flow panel reports pipeline run state as missing without asking for it."""


class RecordingCommunities:
    """Every fetcher answers, with nothing; the ids asked for are kept."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def fetch_values(self, *, community_id: str, fetcher_id: str, **kwargs):
        self.asked.append(fetcher_id)
        return type("Result", (), {"items": []})()


class RecordingDT:
    def __init__(self) -> None:
        self.communities = RecordingCommunities()


async def test_data_flow_never_asks_for_the_pipeline_status_fetcher() -> None:
    # Imported here, not at module level: tests/test_api.py sets the environment before
    # the app is first imported. The API package goes first, because a service module
    # imported alone runs into the services <-> api import cycle.
    import celine.community.api.deps  # noqa: F401
    from celine.community.services.operations import OperationalProvider

    dt = RecordingDT()

    response = await OperationalProvider().data_flow(
        community_key="example-rec", period="7d", dt=dt
    )

    assert "rec_pipeline_status" not in dt.communities.asked
    assert dt.communities.asked  # the device fetchers are still read
    assert response.partial is True
    assert response.missing_sources == ["rec_pipeline_status"]
    assert response.pipelines == []
