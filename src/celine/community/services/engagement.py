"""Gamification and nudging aggregate composition."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from celine.sdk.dt import DTClient
from celine.sdk.nudging import NudgingAdminClient

from celine.community.api.schemas import (
    AntiGamingFlag,
    AntiGamingFlagsResponse,
    LeaderboardEntry,
    NudgeFunnelStep,
    NudgingConversionResponse,
    Period,
    PointsBucket,
    PointsDistributionResponse,
    PointsLedgerEntry,
    PointsLedgerResponse,
)

logger = logging.getLogger(__name__)
ROME = ZoneInfo("Europe/Rome")


def _now() -> datetime:
    return datetime.now(ROME)


def _days(period: Period) -> int:
    return {"today": 1, "7d": 7, "30d": 30}[period]


def _rows(result: Any) -> list[dict[str, Any]]:
    raw = getattr(result, "items", []) or []
    return [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in raw]


async def _fetch(
    dt: DTClient,
    community_key: str,
    fetcher_id: str,
    period: Period,
    **payload: Any,
) -> list[dict[str, Any]] | None:
    end = _now()
    start = end - timedelta(days=_days(period))
    try:
        result = await dt.communities.fetch_values(
            community_id=community_key,
            fetcher_id=fetcher_id,
            payload={"start": start.isoformat(), "end": end.isoformat(), **payload},
            limit=10_000,
        )
        return _rows(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetcher %s unavailable for %s: %s", fetcher_id, community_key, exc)
        return None


def _step(step_id: str, count: int, previous: int | None = None) -> NudgeFunnelStep:
    conversion = 100 if previous is None else (count / previous * 100 if previous else 0)
    return NudgeFunnelStep(id=step_id, count=count, conversion_percent=round(conversion, 1))


def _funnel(
    sent: int, delivered: int, read: int, clicked: int, committed: int
) -> list[NudgeFunnelStep]:
    return [
        _step("sent", sent),
        _step("delivered", delivered, sent),
        _step("read", read, delivered),
        _step("clicked", clicked, read),
        _step("committed", committed, clicked),
    ]


class GamificationProvider:
    async def distribution(
        self, community_key: str, period: Period, dt: DTClient
    ) -> PointsDistributionResponse:
        distribution = await _fetch(dt, community_key, "rec_points_distribution", period)
        leaderboard_rows = await _fetch(
            dt, community_key, "rec_points_leaderboard_community", period
        )
        missing = []
        if distribution is None:
            missing.append("rec_points_distribution")
        if leaderboard_rows is None:
            missing.append("rec_points_leaderboard_community")
        rows = distribution or []
        summary = rows[0] if rows else {}
        buckets = [
            PointsBucket(
                label=str(item.get("label") or item.get("bucket") or "—"),
                minimum=float(item.get("minimum") or item.get("bucket_min") or 0),
                maximum=(
                    float(item["maximum"])
                    if item.get("maximum") is not None
                    else (float(item["bucket_max"]) if item.get("bucket_max") is not None else None)
                ),
                count=int(item.get("count") or 0),
            )
            for item in rows
        ]
        leaderboard = [
            LeaderboardEntry(
                rank=int(item.get("rank") or index + 1),
                device_id=str(item.get("device_id")),
                points=float(item.get("points") or 0),
                trend=int(item.get("trend") or 0),
            )
            for index, item in enumerate(leaderboard_rows or [])
            if item.get("device_id")
        ]
        monitored = int(summary.get("monitored_devices") or 0)
        awarded = int(summary.get("awarded_devices") or 0)
        return PointsDistributionResponse(
            community_key=community_key,
            period=period,
            partial=bool(missing),
            missing_sources=missing,
            monitored_devices=monitored,
            awarded_devices=awarded,
            coverage_percent=round(awarded / monitored * 100, 1) if monitored else 0,
            median_points=float(summary.get("median_points") or 0),
            top_decile_points=float(summary.get("top_decile_points") or 0),
            bottom_decile_points=float(summary.get("bottom_decile_points") or 0),
            concentration_index=float(summary.get("concentration_index") or 0),
            buckets=buckets,
            leaderboard=leaderboard,
        )

    async def flags(
        self, community_key: str, period: Period, dt: DTClient
    ) -> AntiGamingFlagsResponse:
        rows = await _fetch(dt, community_key, "rec_anti_gaming_flags_community", period)
        items = []
        for item in rows or []:
            device_id = item.get("device_id")
            if not device_id:
                continue
            raw_id = str(
                item.get("id") or f"{device_id}:{item.get('rule')}:{item.get('occurred_at')}"
            )
            flag_id = UUID(raw_id) if len(raw_id) == 36 else uuid5(NAMESPACE_URL, raw_id)
            severity = str(item.get("severity") or "medium")
            items.append(
                AntiGamingFlag(
                    id=flag_id,
                    device_id=str(device_id),
                    rule=str(item.get("rule") or "anti-gaming"),
                    severity=severity
                    if severity in {"low", "medium", "high", "critical"}
                    else "medium",
                    detail=str(item.get("detail") or "Anomalia nel calcolo dei punti."),
                    observed_value=float(item.get("observed_value") or 0),
                    threshold=float(item.get("threshold") or 0),
                    occurred_at=datetime.fromisoformat(
                        str(item.get("occurred_at") or _now().isoformat())
                    ),
                )
            )
        return AntiGamingFlagsResponse(
            community_key=community_key,
            partial=rows is None,
            missing_sources=[] if rows is not None else ["rec_anti_gaming_flags_community"],
            items=items,
        )

    async def ledger(
        self, community_key: str, device_id: str, period: Period, dt: DTClient
    ) -> PointsLedgerResponse | None:
        rows = await _fetch(
            dt, community_key, "rec_device_points_ledger", period, device_id=device_id
        )
        if not rows:
            return None
        entries: list[PointsLedgerEntry] = []
        for index, item in enumerate(rows):
            occurred_at = datetime.fromisoformat(str(item.get("occurred_at") or _now().isoformat()))
            entry_id = str(item.get("id") or index)
            source_ref = str(item.get("source_ref") or "—")
            if "daily_settlement_points" in item or "daily_bonus_points" in item:
                for kind, key, suffix, description in (
                    ("settlement", "daily_settlement_points", "", "Settlement points"),
                    ("bonus", "daily_bonus_points", "-bonus", "Bonus points"),
                ):
                    points = float(item.get(key) or 0)
                    if points:
                        entries.append(
                            PointsLedgerEntry(
                                id=f"{entry_id}{suffix}",
                                occurred_at=occurred_at,
                                kind=kind,
                                source_ref=source_ref,
                                points=points,
                                description=description,
                            )
                        )
                continue
            entries.append(
                PointsLedgerEntry(
                    id=entry_id,
                    occurred_at=occurred_at,
                    kind=str(item.get("kind") or "settlement"),
                    source_ref=source_ref,
                    points=float(item.get("points") or 0),
                    description=str(item.get("description") or ""),
                )
            )
        settlement = sum(item.points for item in entries if item.kind == "settlement")
        bonus = sum(item.points for item in entries if item.kind == "bonus")
        caps = sum(item.points for item in entries if item.kind == "cap")
        return PointsLedgerResponse(
            community_key=community_key,
            device_id=device_id,
            settlement_points=settlement,
            bonus_points=bonus,
            cap_adjustments=caps,
            total_points=settlement + bonus + caps,
            entries=entries,
        )


class NudgingProvider:
    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return dict(value)

    @staticmethod
    def _severity(value: Any) -> str:
        return {
            "info": "low",
            "warning": "medium",
            "alert": "high",
            "critical": "critical",
        }.get(str(value or "").lower(), "medium")

    async def conversion(
        self, community_key: str, period: Period, nudging: NudgingAdminClient
    ) -> NudgingConversionResponse:
        end = _now().date()
        start = end - timedelta(days=_days(period) - 1)
        try:
            raw = await nudging.get_community_analytics(
                community_key,
                start=start,
                end=end,
            )
            source = self._mapping(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Nudging analytics unavailable for %s: %s", community_key, exc)
            source = None

        missing = [] if source is not None else ["nudging.analytics"]
        source = source or {}
        total_steps = {
            item["id"]: int(item.get("count") or 0)
            for item in (self._mapping(step) for step in source.get("steps", []))
        }
        totals = {
            key: total_steps.get(key, 0)
            for key in ("sent", "delivered", "read", "clicked", "committed")
        }
        steps = _funnel(**totals)
        rules = []
        for raw_rule in source.get("rules", []):
            item = self._mapping(raw_rule)
            rule_counts = {
                step["id"]: int(step.get("count") or 0)
                for step in (self._mapping(value) for value in item.get("steps", []))
            }
            last_fired_at = item.get("last_fired_at")
            if isinstance(last_fired_at, str):
                last_fired_at = datetime.fromisoformat(last_fired_at)
            rules.append(
                {
                    "id": str(item.get("id") or "unknown"),
                    "name": str(item.get("name") or item.get("id") or "Unknown rule"),
                    "family": str(item.get("family") or "other"),
                    "channel": "email" if item.get("channel") == "email" else "webpush",
                    "severity": self._severity(item.get("severity")),
                    "active": bool(item.get("active")),
                    "last_fired_at": last_fired_at,
                    "volume": int(item.get("volume") or 0),
                    "steps": _funnel(
                        **{
                            key: rule_counts.get(key, 0)
                            for key in ("sent", "delivered", "read", "clicked", "committed")
                        }
                    ),
                }
            )

        failures = []
        for raw_failure in source.get("failures", []):
            item = self._mapping(raw_failure)
            failures.append(
                {
                    "channel": "email" if item.get("channel") == "email" else "webpush",
                    "error_class": str(item.get("error_class") or "delivery_failed"),
                    "count": int(item.get("count") or 0),
                }
            )

        reachability = []
        for raw_reachability in source.get("reachability", []):
            item = self._mapping(raw_reachability)
            reachable = int(item.get("reachable") or 0)
            total = int(item.get("total") or 0)
            reachability.append(
                {
                    "channel": "email" if item.get("channel") == "email" else "webpush",
                    "reachable": reachable,
                    "total": total,
                    "reachable_percent": round(reachable / total * 100, 1) if total else 0,
                    "opted_out": int(item.get("opted_out") or 0),
                }
            )

        return NudgingConversionResponse(
            community_key=community_key,
            period=period,
            partial=bool(missing),
            missing_sources=missing,
            steps=steps,
            click_to_commit_percent=(
                round(totals["committed"] / totals["clicked"] * 100, 1) if totals["clicked"] else 0
            ),
            rules=rules,
            failures=failures,
            reachability=reachability,
        )
