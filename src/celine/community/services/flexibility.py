"""Flexibility oversight and end-to-end pathway composition."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from celine.sdk.dt import DTClient

from celine.community.api.schemas import (
    ChainStep,
    ChainStepId,
    CorrelationState,
    DemonstrationChainResponse,
    DemonstrationReachResponse,
    DemonstrationSummaryResponse,
    DeviceWindowOutcome,
    FlexibilityUptakeResponse,
    FlexibilityWindow,
    FlexibilityWindowDetailResponse,
    FlexibilityWindowsResponse,
    FlexibilityWindowState,
    Period,
)

logger = logging.getLogger(__name__)
ROME = ZoneInfo("Europe/Rome")
STEP_IDS: tuple[ChainStepId, ...] = (
    "offered",
    "nudged",
    "read",
    "opened",
    "committed",
    "delivered",
    "points",
)


def _now() -> datetime:
    return datetime.now(ROME)


def _number(item: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if item.get(key) is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _parse_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=ROME) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return fallback


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
    days = {"today": 1, "7d": 7, "30d": 30}[period]
    start = end - timedelta(days=days)
    try:
        result = await dt.communities.fetch_values(
            community_id=community_key,
            fetcher_id=fetcher_id,
            payload={"start": start.isoformat(), "end": end.isoformat()},
            limit=10_000,
        )
        return _dict_items(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetcher %s unavailable for %s: %s", fetcher_id, community_key, exc)
        return None


def _steps(counts: dict[ChainStepId, int]) -> list[ChainStep]:
    result: list[ChainStep] = []
    previous = 0
    for index, step_id in enumerate(STEP_IDS):
        count = counts.get(step_id, 0)
        conversion = 100.0 if index == 0 else (count / previous * 100 if previous else 0)
        result.append(
            ChainStep(
                id=step_id,
                count=count,
                conversion_percent=round(conversion, 1),
                drop_off=0 if index == 0 else max(0, previous - count),
            )
        )
        previous = count
    return result


def _weakest(steps: list[ChainStep]) -> ChainStepId | None:
    candidates = [step for step in steps[1:] if step.count > 0 or step.drop_off > 0]
    if not candidates:
        return None
    weakest = min(candidates, key=lambda step: step.conversion_percent)
    return weakest.id if weakest.conversion_percent < 70 else None


def _counts_from_outcomes(
    outcomes: list[DeviceWindowOutcome], offered: int = 12
) -> dict[ChainStepId, int]:
    return {
        "offered": offered,
        "nudged": sum(item.nudged for item in outcomes),
        "read": sum(item.read for item in outcomes),
        "opened": sum(item.opened for item in outcomes),
        "committed": sum(item.committed for item in outcomes),
        "delivered": sum((item.delivered_kwh or 0) > 0 for item in outcomes),
        "points": sum(item.points is not None for item in outcomes),
    }


def _bool(item: dict[str, Any], key: str) -> bool:
    value = item.get(key)
    return bool(value) and str(value).lower() not in {"false", "0", "none"}


def _real_outcome(item: dict[str, Any]) -> DeviceWindowOutcome | None:
    device_id = item.get("device_id")
    if not device_id:
        return None
    delivered = item.get("delivered_kwh")
    if delivered is None:
        delivered = item.get("actual_kwh")
    baseline = item.get("baseline_kwh")
    effort = item.get("effort_multiplier")
    points = item.get("points")
    if points is None:
        points = item.get("reward_points_actual")
    committed = _bool(item, "committed") or bool(item.get("committed_at"))
    state: CorrelationState = "complete"
    if not {"nudged", "read", "opened"}.issubset(item) or (
        committed and (delivered is None or baseline is None or points is None)
    ):
        state = "partial"
    return DeviceWindowOutcome(
        device_id=str(device_id),
        nudged=_bool(item, "nudged") or bool(item.get("notification_id")),
        read=_bool(item, "read") or bool(item.get("read_at")),
        opened=_bool(item, "opened") or bool(item.get("opened_at")),
        committed=committed,
        delivered_kwh=float(delivered) if delivered is not None else None,
        baseline_kwh=float(baseline) if baseline is not None else None,
        effort_multiplier=float(effort) if effort is not None else None,
        points=float(points) if points is not None else None,
        correlation_state=state,
    )


class FlexibilityProvider:
    async def _source(
        self, community_key: str, period: Period, dt: DTClient
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        windows = await _fetch(dt, community_key, "rec_flexibility_windows_history", period)
        chain = await _fetch(dt, community_key, "rec_flexibility_chain_daily", period)
        missing = []
        if windows is None:
            missing.append("rec_flexibility_windows_history")
        if chain is None:
            missing.append("rec_flexibility_chain_daily")
        elif any(not {"nudged", "read", "opened"}.issubset(item) for item in chain):
            missing.append("rec_nudge_events")
        return windows or [], chain or [], missing

    async def windows(
        self, community_key: str, period: Period, dt: DTClient
    ) -> FlexibilityWindowsResponse:
        rows, chain, missing = await self._source(community_key, period, dt)
        now = _now()
        items = []
        for row in rows:
            window_id = str(row.get("window_id") or row.get("id") or "unknown")
            related = [item for item in chain if str(item.get("window_id")) == window_id]
            committed = sum(_number(item, "committed_kwh") for item in related)
            delivered = sum(_number(item, "delivered_kwh", "actual_kwh") for item in related)
            start = _parse_datetime(row.get("window_start") or row.get("start"), now)
            end = _parse_datetime(
                row.get("window_end") or row.get("end"), start + timedelta(hours=1)
            )
            raw_state = str(row.get("state") or row.get("status") or "closed").lower()
            state = cast(
                FlexibilityWindowState,
                raw_state if raw_state in {"upcoming", "open", "closed", "settled"} else "closed",
            )
            outcomes = [outcome for item in related if (outcome := _real_outcome(item)) is not None]
            incomplete = sum(item.correlation_state != "complete" for item in outcomes)
            correlation_state: CorrelationState = "complete"
            if "rec_flexibility_chain_daily" in missing or (state != "upcoming" and not outcomes):
                correlation_state = "missing"
            elif incomplete:
                correlation_state = "partial"
            items.append(
                FlexibilityWindow(
                    id=window_id,
                    start=start,
                    end=end,
                    state=state,
                    offered_kwh=_number(row, "offered_kwh", "community_kwh"),
                    committed_kwh=committed,
                    delivered_kwh=delivered,
                    confidence=_number(row, "confidence") or None,
                    model=row.get("flexibility_model") or row.get("model"),
                    participating_devices=sum(item.committed for item in outcomes),
                    delivery_rate=round(delivered / committed * 100, 1) if committed else 0,
                    correlation_state=correlation_state,
                )
            )
        return FlexibilityWindowsResponse(
            community_key=community_key,
            period=period,
            partial=bool(missing) or any(item.correlation_state != "complete" for item in items),
            missing_sources=missing,
            items=items,
        )

    async def detail(
        self, community_key: str, window_id: str, period: Period, dt: DTClient
    ) -> FlexibilityWindowDetailResponse | None:
        windows = await self.windows(community_key, period, dt)
        window = next((item for item in windows.items if item.id == window_id), None)
        if window is None:
            return None
        _, chain_rows, missing = await self._source(community_key, period, dt)
        outcomes = [
            outcome
            for item in chain_rows
            if str(item.get("window_id")) == window_id
            if (outcome := _real_outcome(item)) is not None
        ]
        steps = _steps(_counts_from_outcomes(outcomes, offered=len(outcomes)))
        complete = sum(item.correlation_state == "complete" for item in outcomes)
        correlation = complete / len(outcomes) * 100 if outcomes else 0
        return FlexibilityWindowDetailResponse(
            community_key=community_key,
            partial=bool(missing) or correlation < 100,
            missing_sources=missing,
            window=window,
            steps=steps,
            devices=outcomes,
            correlation_percent=round(correlation, 1),
            weakest_step=_weakest(steps),
            narrative=(
                f"{steps[4].count} dispositivi hanno aderito e {steps[5].count} hanno consegnato "
                f"energia; correlazione completa per il {correlation:.0f}%."
            ),
        )

    async def chain(
        self, community_key: str, period: Period, dt: DTClient
    ) -> DemonstrationChainResponse:
        windows, rows, missing = await self._source(community_key, period, dt)
        outcomes = [outcome for item in rows if (outcome := _real_outcome(item)) is not None]
        steps = _steps(_counts_from_outcomes(outcomes, offered=len(outcomes)))
        efforts = [item.effort_multiplier for item in outcomes if item.effort_multiplier]
        complete = sum(item.correlation_state == "complete" for item in outcomes)
        correlation = complete / len(outcomes) * 100 if outcomes else 0
        offered = sum(_number(item, "offered_kwh", "community_kwh") for item in windows)
        committed = sum(_number(item, "committed_kwh") for item in rows)
        delivered = sum(_number(item, "delivered_kwh", "actual_kwh") for item in rows)
        average = sum(efforts) / len(efforts) if efforts else None
        return DemonstrationChainResponse(
            community_key=community_key,
            period=period,
            partial=bool(missing) or correlation < 100,
            missing_sources=missing,
            steps=steps,
            offered_kwh=offered,
            committed_kwh=committed,
            delivered_kwh=delivered,
            points_awarded=sum(item.points or 0 for item in outcomes),
            average_effort_multiplier=round(average, 2) if average else None,
            correlation_percent=round(correlation, 1),
            weak_step=_weakest(steps),
            summary=(
                f"{len(windows)} finestre hanno raccolto {committed:.1f} kWh di impegni e "
                f"consegnato {delivered:.1f} kWh attraverso il percorso osservato."
            ),
        )

    async def uptake(
        self, community_key: str, period: Period, dt: DTClient
    ) -> FlexibilityUptakeResponse:
        windows = await self.windows(community_key, period, dt)
        offered = sum(item.offered_kwh for item in windows.items if item.state == "settled")
        committed = sum(item.committed_kwh for item in windows.items if item.state == "settled")
        delivered = sum(item.delivered_kwh for item in windows.items if item.state == "settled")
        return FlexibilityUptakeResponse(
            community_key=community_key,
            period=period,
            offered_kwh=round(offered, 2),
            committed_kwh=round(committed, 2),
            delivered_kwh=round(delivered, 2),
            uptake_percent=round(committed / offered * 100, 1) if offered else 0,
            delivery_percent=round(delivered / committed * 100, 1) if committed else 0,
            settled_windows=sum(item.state == "settled" for item in windows.items),
        )

    async def reach(
        self, community_key: str, period: Period, dt: DTClient
    ) -> DemonstrationReachResponse:
        chain = await self.chain(community_key, period, dt)
        values = {step.id: step.count for step in chain.steps}
        return DemonstrationReachResponse(
            community_key=community_key,
            period=period,
            monitored_devices=values.get("offered", 0),
            reachable=values.get("offered", 0),
            nudged=values.get("nudged", 0),
            read=values.get("read", 0),
            acted=values.get("committed", 0),
        )

    async def summary(
        self, community_key: str, period: Period, dt: DTClient
    ) -> DemonstrationSummaryResponse:
        chain = await self.chain(community_key, period, dt)
        windows = await self.windows(community_key, period, dt)
        participating = next((step.count for step in chain.steps if step.id == "delivered"), 0)
        monitored = next((step.count for step in chain.steps if step.id == "offered"), 0)
        return DemonstrationSummaryResponse(
            community_key=community_key,
            period=period,
            windows=sum(item.state == "settled" for item in windows.items),
            committed_kwh=chain.committed_kwh,
            delivered_kwh=chain.delivered_kwh,
            participating_devices=participating,
            monitored_devices=monitored,
            average_effort_multiplier=chain.average_effort_multiplier,
            statement=chain.summary,
        )
