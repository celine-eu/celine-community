"""Community overview composition.

The provider keeps the BFF thin: Digital Twin fetchers supply pre-aggregated values, while this
module only aligns periods, labels results, and reports missing downstream sources explicitly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from celine.sdk.dt import DTClient
from celine.sdk.rec_registry import RecRegistryAdminClient

from celine.community.api.schemas import (
    EnergyPoint,
    Kpi,
    MeterHealth,
    OverviewResponse,
    Period,
    PopulationSummary,
    WindowStep,
    WindowStory,
)

logger = logging.getLogger(__name__)
ROME = ZoneInfo("Europe/Rome")


def _period_bounds(period: Period) -> tuple[datetime, datetime, datetime, datetime]:
    end = datetime.now(ROME)
    if period == "today":
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = end - timedelta(days=7 if period == "7d" else 30)
    duration = end - start
    return start, end, start - duration, start


def _dict_items(result: Any) -> list[dict[str, Any]]:
    raw_items = getattr(result, "items", []) or []
    return [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in raw_items]


async def _fetch(
    dt: DTClient,
    community_key: str,
    fetcher_id: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]] | None:
    try:
        result = await dt.communities.fetch_values(
            community_id=community_key,
            fetcher_id=fetcher_id,
            payload={"start": start.isoformat(), "end": end.isoformat()},
            limit=10_000,
        )
        return _dict_items(result)
    # A degraded downstream must produce an explicit partial response, independently of the
    # concrete exception raised by the generated SDK or its HTTP transport.
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetcher %s unavailable for %s: %s", fetcher_id, community_key, exc)
        return None


def _number(item: dict[str, Any], key: str) -> float:
    try:
        return float(item.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _totals(items: list[dict[str, Any]]) -> tuple[float, float, float]:
    return (
        sum(_number(item, "total_consumption_kwh") for item in items),
        sum(_number(item, "total_production_kwh") for item in items),
        sum(_number(item, "self_consumption_kwh") for item in items),
    )


def _change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return round(((current - previous) / previous) * 100, 1)


def _energy_points(items: list[dict[str, Any]], period: Period) -> list[EnergyPoint]:
    points: list[EnergyPoint] = []
    for index, item in enumerate(items):
        raw_ts = item.get("ts") or item.get("date")
        try:
            timestamp = datetime.fromisoformat(str(raw_ts))
            label = timestamp.astimezone(ROME).strftime("%H:%M" if period == "today" else "%d/%m")
        except (TypeError, ValueError):
            label = str(index + 1)
        points.append(
            EnergyPoint(
                label=label,
                import_kwh=_number(item, "total_consumption_kwh"),
                export_kwh=_number(item, "total_production_kwh"),
                shared_kwh=_number(item, "self_consumption_kwh"),
            )
        )
    return points


def _derived_name(community_key: str) -> str:
    return community_key.replace("-", " ").replace("_", " ").title()


async def _community_name(registry: RecRegistryAdminClient, community_key: str) -> str:
    """The REC's name as the registry records it.

    The registry is where a REC's name lives — the alias is an identifier, not a
    label. Falling back to a title-cased key keeps the page readable when the
    registry is down, which is the same call that already decides whether the
    population figures are present.
    """
    try:
        response = await registry.get_community(community_key)
        community = getattr(response, "parsed", None)
        name = getattr(community, "name", None)
        if isinstance(name, str) and name:
            return name
    except Exception as exc:  # noqa: BLE001
        logger.warning("REC Registry name unavailable for %s: %s", community_key, exc)
    return _derived_name(community_key)


async def _administrative_members(
    registry: RecRegistryAdminClient,
    community_key: str,
) -> int | None:
    """Count active registry members without retaining participant records."""
    try:
        count = 0
        cursor: str | None = None
        while True:
            response = await registry.list_members(
                community_key,
                status="active",
                limit=100,
                cursor=cursor,
            )
            page = getattr(response, "parsed", None)
            if page is None:
                raise RuntimeError(f"REC Registry returned HTTP {response.status_code}")
            count += len(page.items)
            next_cursor = getattr(page, "next_cursor", None)
            if not isinstance(next_cursor, str) or not next_cursor:
                return count
            cursor = next_cursor
    except Exception as exc:  # noqa: BLE001
        logger.warning("REC Registry population unavailable for %s: %s", community_key, exc)
        return None


def _population(
    items: list[dict[str, Any]] | None,
    administrative_members: int | None,
) -> PopulationSummary:
    item = items[-1] if items else {}
    monitored_members = int(_number(item, "monitored_members"))
    registry_members = (
        administrative_members
        if administrative_members is not None
        else int(_number(item, "administrative_members"))
    )
    raw_unregistered = item.get("unregistered_meters")
    unregistered_meters = (
        int(_number(item, "unregistered_meters"))
        if raw_unregistered is not None
        else max(registry_members - monitored_members, 0)
    )
    return PopulationSummary(
        administrative_members=registry_members,
        monitored_members=monitored_members,
        monitored_devices=int(_number(item, "monitored_devices")),
        unregistered_meters=unregistered_meters,
    )


def _meter_health(items: list[dict[str, Any]] | None) -> MeterHealth:
    item = items[-1] if items else {}
    return MeterHealth(
        reporting=int(_number(item, "reporting")),
        degraded=int(_number(item, "degraded")),
        silent=int(_number(item, "silent")),
    )


def _empty_window() -> WindowStory:
    return WindowStory(
        id="unavailable",
        start=datetime.now(ROME),
        offered_kwh=0,
        delivered_kwh=0,
        baseline_multiplier=0,
        steps=[
            WindowStep(id=value, value=0)
            for value in ("offered", "nudged", "read", "committed", "delivered", "points")
        ],
    )


def _truthy(item: dict[str, Any], key: str) -> bool:
    value = item.get(key)
    return bool(value) and str(value).lower() not in {"false", "0", "none"}


def _window_story(windows: list[dict[str, Any]], chain: list[dict[str, Any]]) -> WindowStory:
    if not windows:
        return _empty_window()
    window = windows[0]
    window_id = str(window.get("window_id") or window.get("id") or "unavailable")
    outcomes = [item for item in chain if str(item.get("window_id")) == window_id]
    delivered = sum(_number(item, "delivered_kwh") for item in outcomes)
    baseline = sum(_number(item, "baseline_kwh") for item in outcomes)
    raw_start = window.get("window_start") or window.get("start")
    try:
        start = datetime.fromisoformat(str(raw_start))
        if start.tzinfo is None:
            start = start.replace(tzinfo=ROME)
    except (TypeError, ValueError):
        start = datetime.now(ROME)
    values = {
        "offered": len(outcomes),
        "nudged": sum(_truthy(item, "nudged") for item in outcomes),
        "read": sum(_truthy(item, "read") for item in outcomes),
        "committed": sum(_truthy(item, "committed") for item in outcomes),
        "delivered": sum(_number(item, "delivered_kwh") > 0 for item in outcomes),
        "points": sum(item.get("points") is not None for item in outcomes),
    }
    return WindowStory(
        id=window_id,
        start=start,
        offered_kwh=_number(window, "offered_kwh"),
        delivered_kwh=delivered,
        baseline_multiplier=round(delivered / baseline, 2) if baseline else 0,
        steps=[WindowStep(id=step_id, value=value) for step_id, value in values.items()],
    )


class OverviewProvider:
    async def get(
        self,
        *,
        community_key: str,
        period: Period,
        dt: DTClient,
        registry: RecRegistryAdminClient,
        community_name: str | None = None,
    ) -> OverviewResponse:
        community_name = community_name or await _community_name(registry, community_key)
        start, end, previous_start, previous_end = _period_bounds(period)
        energy_fetcher = (
            "rec_self_consumption" if period == "today" else "rec_self_consumption_daily"
        )
        current = await _fetch(dt, community_key, energy_fetcher, start, end)
        previous = await _fetch(dt, community_key, energy_fetcher, previous_start, previous_end)
        population_items = await _fetch(dt, community_key, "rec_population_summary", start, end)
        meter_items = await _fetch(dt, community_key, "rec_meters_health_summary", start, end)
        window_items = await _fetch(
            dt, community_key, "rec_flexibility_windows_history", start, end
        )
        chain_items = await _fetch(dt, community_key, "rec_flexibility_chain_daily", start, end)
        administrative_members = await _administrative_members(registry, community_key)

        missing: list[str] = []
        if current is None:
            missing.append(energy_fetcher)
            current = []
        if previous is None:
            missing.append(f"{energy_fetcher}:previous")
            previous = []
        if population_items is None:
            missing.append("rec_population_summary")
        if administrative_members is None and (
            not population_items
            or population_items[-1].get("administrative_members") is None
            or population_items[-1].get("unregistered_meters") is None
        ):
            missing.append("rec_registry_population")
        if meter_items is None:
            missing.append("rec_meters_health_summary")
        if window_items is None:
            missing.append("rec_flexibility_windows_history")
        if chain_items is None:
            missing.append("rec_flexibility_chain_daily")
        elif any(not {"nudged", "read"}.issubset(item) for item in chain_items):
            missing.append("rec_nudge_events")

        imported, exported, shared = _totals(current)
        prev_imported, prev_exported, prev_shared = _totals(previous)
        ratio = (shared / exported * 100) if exported else 0
        prev_ratio = (prev_shared / prev_exported * 100) if prev_exported else 0

        return OverviewResponse(
            community_key=community_key,
            community_name=community_name,
            period=period,
            updated_at=datetime.now(ROME),
            partial=bool(missing),
            missing_sources=missing,
            kpis=[
                Kpi(
                    id="import", value=imported, unit="kWh", change=_change(imported, prev_imported)
                ),
                Kpi(
                    id="export", value=exported, unit="kWh", change=_change(exported, prev_exported)
                ),
                Kpi(id="shared", value=shared, unit="kWh", change=_change(shared, prev_shared)),
                Kpi(id="ratio", value=ratio, unit="%", change=_change(ratio, prev_ratio)),
            ],
            energy=_energy_points(current, period),
            population=_population(population_items, administrative_members),
            meter_health=_meter_health(meter_items),
            objectives=[],
            window_story=_window_story(window_items or [], chain_items or []),
        )
