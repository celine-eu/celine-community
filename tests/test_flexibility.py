"""Flexibility windows are identified by their bounds, not by an id the Digital Twin mints."""

from celine.community.services.flexibility import FlexibilityProvider, window_id

START = "2026-09-15T10:00:00+00:00"
END = "2026-09-15T11:00:00+00:00"

WINDOWS = [
    {"window_start": START, "window_end": END, "offered_kwh": 5.0, "state": "settled"},
    {
        "window_start": "2026-09-15T12:00:00+00:00",
        "window_end": "2026-09-15T13:00:00+00:00",
        "offered_kwh": 3.0,
        "state": "settled",
    },
]
CHAIN = [
    {
        "window_start": START,
        "window_end": END,
        "device_id": "device-1",
        "committed": True,
        "committed_kwh": 2.0,
        "delivered_kwh": 1.5,
        "baseline_kwh": 2.0,
        "points": 10,
    },
]


class Communities:
    async def fetch_values(self, *, fetcher_id: str, **kwargs):
        items = {
            "rec_flexibility_windows_history": WINDOWS,
            "rec_flexibility_chain_daily": CHAIN,
        }[fetcher_id]
        return type("Result", (), {"items": items})()


class DT:
    communities = Communities()


def test_the_id_is_derived_from_the_bounds_and_is_stable() -> None:
    derived = window_id({"window_start": START, "window_end": END})

    assert derived == window_id({"window_start": START, "window_end": END, "device_id": "x"})
    assert derived != window_id({"window_start": START, "window_end": "other"})
    assert window_id({"window_id": "supplied", "window_start": START}) == "supplied"
    assert window_id({"window_start": START}) == "unknown"


async def test_chain_rows_join_the_window_they_share_bounds_with() -> None:
    response = await FlexibilityProvider().windows("example_rec", "30d", DT())

    first, second = response.items
    assert first.id == window_id(WINDOWS[0])
    assert first.committed_kwh == 2.0
    assert first.delivered_kwh == 1.5
    assert second.committed_kwh == 0


async def test_the_derived_id_opens_the_window_detail() -> None:
    requested = window_id(WINDOWS[0])

    detail = await FlexibilityProvider().detail("example_rec", requested, "30d", DT())

    assert detail is not None
    assert detail.window.id == requested
    assert [device.device_id for device in detail.devices] == ["device-1"]
