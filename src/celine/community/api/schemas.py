"""Pydantic schemas exposed by the Community Manager BFF."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


Period = Literal["today", "7d", "30d"]
KpiId = Literal["import", "export", "shared", "ratio"]
ObjectiveId = Literal["shared", "uptake", "delivery", "participation", "co2"]
DeviceStatus = Literal["reporting", "degraded", "silent"]
EngagementState = Literal["active", "dormant", "never-activated"]
DeviceSort = Literal[
    "device_id",
    "last_seen",
    "gap_minutes",
    "coverage_percent",
    "points_30d",
]
SortOrder = Literal["asc", "desc"]
PipelineState = Literal["success", "running", "failed", "stale", "unknown"]
FlexibilityWindowState = Literal["upcoming", "open", "closed", "settled"]
ChainStepId = Literal["offered", "nudged", "read", "opened", "committed", "delivered", "points"]
CorrelationState = Literal["complete", "partial", "missing"]
Severity = Literal["low", "medium", "high", "critical"]
FlagState = Literal["open", "acknowledged"]
AlertState = Literal["open", "acknowledged", "muted"]
NudgeChannel = Literal["webpush", "email"]


class MeUser(ApiModel):
    sub: str
    email: str
    name: str | None = None
    preferred_username: str | None = None
    locale: str | None = None
    organization: str
    community_key: str
    community_name: str
    scopes: list[str]


class MeResponse(ApiModel):
    user: MeUser


class EnergyPoint(ApiModel):
    label: str
    import_kwh: float
    export_kwh: float
    shared_kwh: float


class Kpi(ApiModel):
    id: KpiId
    value: float
    unit: str
    change: float


class ObjectiveProgress(ApiModel):
    id: ObjectiveId
    current: float
    target: float
    unit: str


class PopulationSummary(ApiModel):
    administrative_members: int
    monitored_members: int
    monitored_devices: int
    unregistered_meters: int


class MeterHealth(ApiModel):
    reporting: int
    degraded: int
    silent: int


class WindowStep(ApiModel):
    id: Literal["offered", "nudged", "read", "committed", "delivered", "points"]
    value: int


class WindowStory(ApiModel):
    id: str
    start: datetime
    offered_kwh: float
    delivered_kwh: float
    baseline_multiplier: float
    steps: list[WindowStep]


class OverviewResponse(ApiModel):
    community_key: str
    community_name: str
    period: Period
    updated_at: datetime
    estimated: bool = False
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    kpis: list[Kpi]
    energy: list[EnergyPoint]
    population: PopulationSummary
    meter_health: MeterHealth
    objectives: list[ObjectiveProgress]
    window_story: WindowStory


class ObjectiveValue(ApiModel):
    id: ObjectiveId
    target: float
    unit: str


class ObjectivesResponse(ApiModel):
    community_key: str
    period: str
    objectives: list[ObjectiveValue]


class ObjectivesUpdate(ApiModel):
    period: str = Field(min_length=1, max_length=32)
    objectives: list[ObjectiveValue] = Field(min_length=1)


class ObjectiveRecord(ApiModel):
    id: UUID
    community_key: str
    period: str
    objective_id: ObjectiveId
    target: float
    unit: str
    updated_at: datetime

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class DeviceHealthSummary(ApiModel):
    reporting: int
    degraded: int
    silent: int
    active: int
    dormant: int
    never_activated: int


class DeviceSummary(ApiModel):
    device_id: str
    last_seen: datetime | None = None
    gap_minutes: int = 0
    coverage_percent: float = 0
    points_30d: float = 0
    engagement_state: EngagementState
    meter_status: DeviceStatus


class MeterGap(ApiModel):
    start: datetime
    end: datetime
    size_minutes: int
    expected_intervals: int


class DeviceDetail(DeviceSummary):
    first_seen: datetime | None = None
    received_intervals: int = 0
    expected_intervals: int = 0
    gaps: list[MeterGap] = Field(default_factory=list)


class DeviceListResponse(ApiModel):
    community_key: str
    period: Period
    page: int
    page_size: int
    total: int
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    summary: DeviceHealthSummary
    items: list[DeviceSummary]


class DeviceDetailResponse(ApiModel):
    community_key: str
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    device: DeviceDetail


class MeterGapsResponse(ApiModel):
    community_key: str
    device_id: str
    coverage_percent: float
    gaps: list[MeterGap]


class PipelineRun(ApiModel):
    id: str
    name: str
    state: PipelineState
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    duration_seconds: float | None = None
    freshness_minutes: int | None = None
    message: str | None = None


class DataFlowResponse(ApiModel):
    community_key: str
    period: Period
    updated_at: datetime
    coverage_percent: float
    received_intervals: int
    expected_intervals: int
    gap_count: int
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    pipelines: list[PipelineRun]


class FlexibilityWindow(ApiModel):
    id: str
    start: datetime
    end: datetime
    state: FlexibilityWindowState
    offered_kwh: float
    committed_kwh: float
    delivered_kwh: float
    confidence: float | None = None
    model: str | None = None
    participating_devices: int = 0
    delivery_rate: float = 0
    correlation_state: CorrelationState = "complete"


class FlexibilityWindowsResponse(ApiModel):
    community_key: str
    period: Period
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    items: list[FlexibilityWindow]


class FlexibilityUptakeResponse(ApiModel):
    community_key: str
    period: Period
    offered_kwh: float
    committed_kwh: float
    delivered_kwh: float
    uptake_percent: float
    delivery_percent: float
    settled_windows: int


class ChainStep(ApiModel):
    id: ChainStepId
    count: int
    conversion_percent: float
    drop_off: int


class DemonstrationChainResponse(ApiModel):
    community_key: str
    period: Period
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    steps: list[ChainStep]
    offered_kwh: float
    committed_kwh: float
    delivered_kwh: float
    points_awarded: float
    average_effort_multiplier: float | None = None
    correlation_percent: float
    weak_step: ChainStepId | None = None
    summary: str


class DeviceWindowOutcome(ApiModel):
    device_id: str
    nudged: bool
    read: bool
    opened: bool
    committed: bool
    delivered_kwh: float | None = None
    baseline_kwh: float | None = None
    effort_multiplier: float | None = None
    points: float | None = None
    correlation_state: CorrelationState


class FlexibilityWindowDetailResponse(ApiModel):
    community_key: str
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    window: FlexibilityWindow
    steps: list[ChainStep]
    devices: list[DeviceWindowOutcome]
    correlation_percent: float
    weakest_step: ChainStepId | None = None
    narrative: str


class DemonstrationReachResponse(ApiModel):
    community_key: str
    period: Period
    monitored_devices: int
    reachable: int
    nudged: int
    read: int
    acted: int


class DemonstrationSummaryResponse(ApiModel):
    community_key: str
    period: Period
    windows: int
    committed_kwh: float
    delivered_kwh: float
    participating_devices: int
    monitored_devices: int
    average_effort_multiplier: float | None = None
    statement: str


class PointsBucket(ApiModel):
    label: str
    minimum: float
    maximum: float | None = None
    count: int


class LeaderboardEntry(ApiModel):
    rank: int
    device_id: str
    points: float
    trend: int = 0


class PointsDistributionResponse(ApiModel):
    community_key: str
    period: Period
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    monitored_devices: int
    awarded_devices: int
    coverage_percent: float
    median_points: float
    top_decile_points: float
    bottom_decile_points: float
    concentration_index: float
    buckets: list[PointsBucket]
    leaderboard: list[LeaderboardEntry]


class AntiGamingFlag(ApiModel):
    id: UUID
    device_id: str
    rule: str
    severity: Severity
    detail: str
    observed_value: float
    threshold: float
    occurred_at: datetime
    state: FlagState = "open"


class AntiGamingFlagsResponse(ApiModel):
    community_key: str
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    items: list[AntiGamingFlag]


class PointsLedgerEntry(ApiModel):
    id: str
    occurred_at: datetime
    kind: Literal["settlement", "bonus", "cap"]
    source_ref: str
    points: float
    description: str


class PointsLedgerResponse(ApiModel):
    community_key: str
    device_id: str
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    settlement_points: float
    bonus_points: float
    cap_adjustments: float
    total_points: float
    entries: list[PointsLedgerEntry]


class NudgeFunnelStep(ApiModel):
    id: Literal["sent", "delivered", "read", "clicked", "committed"]
    count: int
    conversion_percent: float


class NudgeRuleMetric(ApiModel):
    id: str
    name: str
    family: str
    channel: NudgeChannel
    severity: Severity
    active: bool
    last_fired_at: datetime | None = None
    volume: int
    steps: list[NudgeFunnelStep]


class DeliveryFailure(ApiModel):
    channel: NudgeChannel
    error_class: str
    count: int


class ReachabilityMetric(ApiModel):
    channel: NudgeChannel
    reachable: int
    total: int
    reachable_percent: float
    opted_out: int


class NudgingConversionResponse(ApiModel):
    community_key: str
    period: Period
    partial: bool = False
    missing_sources: list[str] = Field(default_factory=list)
    steps: list[NudgeFunnelStep]
    click_to_commit_percent: float
    rules: list[NudgeRuleMetric]
    failures: list[DeliveryFailure]
    reachability: list[ReachabilityMetric]


class FeedbackScreenshotPayload(ApiModel):
    mime_type: str = Field(default="image/png", max_length=64)
    data_base64: str = Field(min_length=1)


class FeedbackContextPayload(ApiModel):
    page_url: str = Field(min_length=1)
    page_title: str | None = None
    page_path: str | None = None
    locale: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    user_agent: str | None = None
    viewport_width: int | None = Field(default=None, ge=0)
    viewport_height: int | None = Field(default=None, ge=0)
    screen_width: int | None = Field(default=None, ge=0)
    screen_height: int | None = Field(default=None, ge=0)
    color_scheme: Literal["light", "dark"] | None = None
    client_timestamp: datetime | None = None
    extra: dict = Field(default_factory=dict)


class FeedbackCreateRequest(ApiModel):
    rating: int = Field(ge=0, le=5)
    comment: str = Field(default="", max_length=4000)
    context: FeedbackContextPayload
    screenshot: FeedbackScreenshotPayload | None = None


class FeedbackCreateResponse(ApiModel):
    id: UUID
    created_at: datetime


class ManagerAlertResponse(ApiModel):
    id: UUID
    source: str
    severity: Severity
    title: str
    detail: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    assigned_to: str | None = None
    muted_until: datetime | None = None
    active: bool
    acknowledged: bool
    state: AlertState
    created_at: datetime
    updated_at: datetime


class AlertsResponse(ApiModel):
    community_key: str
    total: int
    items: list[ManagerAlertResponse]


class AlertMuteRequest(ApiModel):
    muted_until: datetime


class AlertAssignRequest(ApiModel):
    assigned_to: str = Field(min_length=1, max_length=255)


class AuditEventResponse(ApiModel):
    id: UUID
    actor_id: str
    action: str
    resource_type: str
    resource_id: str | None = None
    detail: dict
    created_at: datetime
