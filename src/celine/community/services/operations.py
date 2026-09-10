"""Operational device and ingestion health read models.

The service composes governed Digital Twin fetchers and never enriches a device identifier with
participant identity. Missing downstream sources remain visible in every response.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from celine.sdk.dt import DTClient

from celine.community.api.schemas import (
    DataFlowResponse,
    DeviceDetail,
    DeviceDetailResponse,
    DeviceHealthSummary,
    DeviceListResponse,
    DeviceSort,
    DeviceStatus,
    DeviceSummary,
    EngagementState,
    MeterGap,
    MeterGapsResponse,
    Period,
    PipelineRun,
    PipelineState,
    SortOrder,
)

logger = logging.getLogger(__name__)
ROME = ZoneInfo("Europe/Rome")


def _period_days(period: Period) -> int:
    return {"today": 1, "7d": 7, "30d": 30}[period]


def _now() -> datetime:
    return datetime.now(ROME)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=ROME) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _number(item: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if item.get(key) is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _dict_items(result: Any) -> list[dict[str, Any]]:
    raw_items = getattr(result, "items", []) or []
    return [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in raw_items]


async def _fetch(
    dt: DTClient,
    community_key: str,
    fetcher_id: str,
    period: Period,
) -> list[dict[str, Any]] | None:
    end = _now()
    start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if period != "today":
        start = end - timedelta(days=_period_days(period))
    try:
        result = await dt.communities.fetch_values(
            community_id=community_key,
            fetcher_id=fetcher_id,
            payload={"start": start.isoformat(), "end": end.isoformat()},
            limit=10_000,
        )
        return _dict_items(result)
    # Generated SDK and transport failures all mean a partial operational view.
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetcher %s unavailable for %s: %s", fetcher_id, community_key, exc)
        return None


def _status(gap_minutes: int, coverage: float, raw: Any = None) -> DeviceStatus:
    if raw in {"reporting", "degraded", "silent"}:
        return raw
    if gap_minutes >= 24 * 60:
        return "silent"
    if gap_minutes >= 30 or coverage < 98:
        return "degraded"
    return "reporting"


def _engagement(raw: Any) -> EngagementState:
    normalized = str(raw or "dormant").lower().replace("_", "-")
    if normalized in {"active", "dormant", "never-activated"}:
        return cast(EngagementState, normalized)
    return "dormant"


def _build_real_devices(
    meters: list[dict[str, Any]],
    points: list[dict[str, Any]],
    engagement: list[dict[str, Any]],
    period: Period,
) -> list[DeviceDetail]:
    point_map = {
        str(item.get("device_id")): _number(item, "points_30d", "points", "total_points")
        for item in points
        if item.get("device_id")
    }
    engagement_map = {
        str(item.get("device_id")): _engagement(
            item.get("engagement_state") or item.get("device_class")
        )
        for item in engagement
        if item.get("device_id")
    }
    expected_default = _period_days(period) * 96
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in meters:
        device_id = item.get("device_id") or item.get("meter_id")
        if not device_id:
            continue
        grouped.setdefault(str(device_id), []).append(item)

    devices: list[DeviceDetail] = []
    for device_id, rows in grouped.items():
        gap = max(
            int(_number(item, "gap_minutes", "missing_minutes", "largest_gap_minutes"))
            for item in rows
        )
        expected = (
            max(int(_number(item, "expected_intervals")) for item in rows) or expected_default
        )
        received = max(int(_number(item, "received_intervals")) for item in rows)
        coverage = max(_number(item, "coverage_percent", "coverage_pct") for item in rows)
        if not coverage and expected:
            coverage = received / expected * 100
        timestamps = [
            value
            for item in rows
            if (value := _parse_datetime(item.get("last_seen") or item.get("last_valid_at")))
        ]
        first_seen_values = [
            value for item in rows if (value := _parse_datetime(item.get("first_seen")))
        ]
        gaps = []
        for item in rows:
            item_gap = int(_number(item, "gap_minutes", "missing_minutes", "largest_gap_minutes"))
            gap_start = _parse_datetime(item.get("gap_start") or item.get("start"))
            gap_end = _parse_datetime(item.get("gap_end") or item.get("end"))
            if gap_start and gap_end and item_gap:
                gaps.append(
                    MeterGap(
                        start=gap_start,
                        end=gap_end,
                        size_minutes=item_gap,
                        expected_intervals=max(1, item_gap // 15),
                    )
                )
        raw_status = next((item.get("status") for item in rows if item.get("status")), None)
        devices.append(
            DeviceDetail(
                device_id=device_id,
                first_seen=min(first_seen_values) if first_seen_values else None,
                last_seen=max(timestamps) if timestamps else None,
                gap_minutes=gap,
                coverage_percent=round(coverage, 1),
                points_30d=point_map.get(device_id, 0),
                engagement_state=engagement_map.get(device_id, "dormant"),
                meter_status=_status(gap, coverage, raw_status),
                received_intervals=received,
                expected_intervals=expected,
                gaps=gaps,
            )
        )
    return devices


def _health_summary(devices: list[DeviceDetail]) -> DeviceHealthSummary:
    return DeviceHealthSummary(
        reporting=sum(item.meter_status == "reporting" for item in devices),
        degraded=sum(item.meter_status == "degraded" for item in devices),
        silent=sum(item.meter_status == "silent" for item in devices),
        active=sum(item.engagement_state == "active" for item in devices),
        dormant=sum(item.engagement_state == "dormant" for item in devices),
        never_activated=sum(item.engagement_state == "never-activated" for item in devices),
    )


def _sort_value(device: DeviceDetail, sort: DeviceSort):
    if sort == "last_seen":
        return device.last_seen or datetime.min.replace(tzinfo=ROME)
    return getattr(device, sort)


class OperationalProvider:
    async def _devices(
        self,
        community_key: str,
        period: Period,
        dt: DTClient,
    ) -> tuple[list[DeviceDetail], list[str]]:
        meter_items = await _fetch(dt, community_key, "rec_meters_missing_intervals", period)
        point_items = await _fetch(dt, community_key, "rec_points_leaderboard_community", period)
        engagement_items = await _fetch(dt, community_key, "rec_device_streaks", period)
        missing = []
        if meter_items is None:
            missing.append("rec_meters_missing_intervals")
        if point_items is None:
            missing.append("rec_points_leaderboard_community")
        if engagement_items is None:
            missing.append("rec_device_streaks")
        return (
            _build_real_devices(
                meter_items or [],
                point_items or [],
                engagement_items or [],
                period,
            ),
            missing,
        )

    async def export_devices(
        self,
        community_key: str,
        period: Period,
        dt: DTClient,
    ) -> tuple[list[DeviceDetail], list[str]]:
        return await self._devices(community_key, period, dt)

    async def list_devices(
        self,
        *,
        community_key: str,
        period: Period,
        dt: DTClient,
        search: str | None,
        status: DeviceStatus | None,
        engagement: EngagementState | None,
        sort: DeviceSort,
        order: SortOrder,
        page: int,
        page_size: int,
    ) -> DeviceListResponse:
        devices, missing = await self._devices(community_key, period, dt)
        summary = _health_summary(devices)
        filtered = devices
        if search:
            needle = search.casefold()
            filtered = [item for item in filtered if needle in item.device_id.casefold()]
        if status:
            filtered = [item for item in filtered if item.meter_status == status]
        if engagement:
            filtered = [item for item in filtered if item.engagement_state == engagement]
        filtered.sort(key=lambda item: _sort_value(item, sort), reverse=order == "desc")
        start = (page - 1) * page_size
        return DeviceListResponse(
            community_key=community_key,
            period=period,
            page=page,
            page_size=page_size,
            total=len(filtered),
            partial=bool(missing),
            missing_sources=missing,
            summary=summary,
            items=[
                DeviceSummary.model_validate(item.model_dump())
                for item in filtered[start : start + page_size]
            ],
        )

    async def device_detail(
        self,
        *,
        community_key: str,
        device_id: str,
        period: Period,
        dt: DTClient,
    ) -> DeviceDetailResponse | None:
        devices, missing = await self._devices(community_key, period, dt)
        device = next((item for item in devices if item.device_id == device_id), None)
        if device is None:
            return None
        return DeviceDetailResponse(
            community_key=community_key,
            partial=bool(missing),
            missing_sources=missing,
            device=device,
        )

    async def meter_gaps(
        self,
        *,
        community_key: str,
        device_id: str,
        period: Period,
        dt: DTClient,
    ) -> MeterGapsResponse | None:
        detail = await self.device_detail(
            community_key=community_key,
            device_id=device_id,
            period=period,
            dt=dt,
        )
        if detail is None:
            return None
        return MeterGapsResponse(
            community_key=community_key,
            device_id=device_id,
            coverage_percent=detail.device.coverage_percent,
            gaps=detail.device.gaps,
        )

    async def data_flow(
        self,
        *,
        community_key: str,
        period: Period,
        dt: DTClient,
    ) -> DataFlowResponse:
        devices, missing = await self._devices(community_key, period, dt)
        pipeline_items = await _fetch(dt, community_key, "rec_pipeline_status", period)
        if pipeline_items is None:
            missing.append("rec_pipeline_status")
        pipelines = []
        for item in pipeline_items or []:
            raw_state = str(item.get("state") or "unknown").lower()
            state = cast(
                PipelineState,
                raw_state
                if raw_state in {"success", "running", "failed", "stale", "unknown"}
                else "unknown",
            )
            pipelines.append(
                PipelineRun(
                    id=str(item.get("id") or item.get("pipeline_id") or "unknown"),
                    name=str(item.get("name") or item.get("pipeline_name") or "Pipeline"),
                    state=state,
                    last_run_at=_parse_datetime(item.get("last_run_at")),
                    last_success_at=_parse_datetime(item.get("last_success_at")),
                    duration_seconds=_number(item, "duration_seconds") or None,
                    freshness_minutes=int(_number(item, "freshness_minutes")) or None,
                    message=item.get("message"),
                )
            )
        expected = sum(item.expected_intervals for item in devices)
        received = sum(item.received_intervals for item in devices)
        coverage = received / expected * 100 if expected else 0
        return DataFlowResponse(
            community_key=community_key,
            period=period,
            updated_at=_now(),
            coverage_percent=round(coverage, 1),
            received_intervals=received,
            expected_intervals=expected,
            gap_count=sum(len(item.gaps) for item in devices),
            partial=bool(missing),
            missing_sources=missing,
            pipelines=pipelines,
        )
