"""The anti-gaming panel reads the Digital Twin's flag rows as the pipeline writes them."""


class FlagCommunities:
    """The anti-gaming fetcher's rows as the Digital Twin returns them."""

    async def fetch_values(self, *, community_id: str, fetcher_id: str, **kwargs):
        assert fetcher_id == "rec_anti_gaming_flags_community"
        items = [
            {"id": "0123456789abcdef0123456789abcdef", "device_id": "ex-00001",
             "rule": "L4_DAILY_SPIKE", "severity": "warning", "observed_value": 120.0,
             "threshold": 40.0, "occurred_at": "2026-08-06T00:00:00+00:00"},
            {"id": "fedcba9876543210fedcba9876543210", "device_id": "ex-00002",
             "rule": "L3_WINDOW_CAP", "severity": "info", "observed_value": 9.0,
             "threshold": 6.0, "occurred_at": "2026-08-05T00:00:00+00:00"},
        ]
        return type("Result", (), {"items": items})()


class FlagDT:
    communities = FlagCommunities()


async def test_anti_gaming_flags_map_the_pipeline_severities() -> None:
    # Imported here, not at module level: tests/test_api.py sets the environment before
    # the app is first imported. The API package goes first, because a service module
    # imported alone runs into the services <-> api import cycle.
    import celine.community.api.deps  # noqa: F401
    from celine.community.services.engagement import GamificationProvider

    response = await GamificationProvider().flags("example-rec", "30d", FlagDT())

    assert not response.partial
    assert [(f.device_id, f.rule, f.severity) for f in response.items] == [
        ("ex-00001", "L4_DAILY_SPIKE", "medium"),
        ("ex-00002", "L3_WINDOW_CAP", "low"),
    ]
