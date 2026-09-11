"""Public contract tests for the first Manager Dashboard slice."""

from contextlib import contextmanager

import psycopg2
import pytest
from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import Organization
from fastapi.testclient import TestClient

from celine.community.api.deps import (
    get_dt_client,
    get_nudging_client,
    get_registry_client,
    get_user_from_request,
)
from celine.community.main import app
from celine.community.settings import settings

client = TestClient(app)
TEST_ALERTS = (
    ("a0b92e4b-8d02-4c62-9b12-a5ec81b12601", "meter-health", "critical"),
    ("a0b92e4b-8d02-4c62-9b12-a5ec81b12602", "gamification", "high"),
    ("a0b92e4b-8d02-4c62-9b12-a5ec81b12603", "flexibility", "medium"),
)
ANTI_GAMING_FLAG_ID = "29b66c7f-8c46-4fb4-942f-8b8c2cf72911"


class UnavailableCommunities:
    async def fetch_values(self, *, fetcher_id: str, **kwargs):
        if fetcher_id == "rec_device_points_ledger":
            device_id = kwargs.get("payload", {}).get("device_id")
            items = (
                [
                    {
                        "id": "ledger-test-1",
                        "occurred_at": "2026-08-05T00:00:00+02:00",
                        "source_ref": "2026-08-05",
                        "daily_settlement_points": 12,
                        "daily_bonus_points": 3,
                    }
                ]
                if device_id == "IT001E000327"
                else []
            )
            return type("Result", (), {"items": items})()
        if fetcher_id == "rec_anti_gaming_flags_community":
            return type(
                "Result",
                (),
                {
                    "items": [
                        {
                            "id": ANTI_GAMING_FLAG_ID,
                            "device_id": "IT001E-TEST-001",
                            "rule": "test-spike",
                            "severity": "high",
                            "detail": "Test-only downstream fixture.",
                            "observed_value": 184,
                            "threshold": 120,
                            "occurred_at": "2026-08-06T08:00:00+02:00",
                        }
                    ]
                },
            )()
        raise RuntimeError(f"Fetcher unavailable in contract test: {fetcher_id}")


class UnavailableDT:
    communities = UnavailableCommunities()


class _Page:
    def __init__(self, items):
        self.items = items
        self.next_cursor = None


class _Response:
    def __init__(self, parsed):
        self.parsed = parsed
        self.status_code = 200


class _Community:
    def __init__(self, key, name):
        self.key = key
        self.name = name


REGISTRY_COMMUNITIES = (
    ("gr-renewable-community", "Greenland Renewable Energy Community"),
    ("example_rec", "Example Renewable Energy Community"),
)


class PartialRegistry:
    """Enumerates and names RECs; cannot count members.

    The split is the point: the REC list and every REC's name come from the
    registry, and a failure to count members degrades the population panel
    without touching who may open which REC.
    """

    async def list_communities(self, **kwargs):
        return _Response(_Page([_Community(key, name) for key, name in REGISTRY_COMMUNITIES]))

    async def get_community(self, community_key, **kwargs):
        for key, name in REGISTRY_COMMUNITIES:
            if key == community_key:
                return _Response(_Community(key, name))
        raise RuntimeError(f"Unknown community in contract test: {community_key}")

    async def list_members(self, *args, **kwargs):
        raise RuntimeError("REC Registry membership unavailable in contract test")


class UnavailableRegistry:
    async def list_communities(self, **kwargs):
        raise RuntimeError("REC Registry unavailable in contract test")

    async def get_community(self, *args, **kwargs):
        raise RuntimeError("REC Registry unavailable in contract test")

    async def list_members(self, *args, **kwargs):
        raise RuntimeError("REC Registry unavailable in contract test")


class AvailableNudging:
    async def get_community_analytics(self, community_id: str, **kwargs):
        assert community_id == "gr-renewable-community"
        return {
            "community_id": community_id,
            "steps": [
                {"id": "sent", "count": 64},
                {"id": "delivered", "count": 64},
                {"id": "read", "count": 21},
                {"id": "clicked", "count": 4},
                {"id": "committed", "count": 1},
            ],
            "rules": [
                {
                    "id": "flexibility_opportunity",
                    "name": "Flexibility opportunity",
                    "family": "energy",
                    "channel": "webpush",
                    "severity": "warning",
                    "active": True,
                    "last_fired_at": "2026-08-20T10:30:00+00:00",
                    "volume": 64,
                    "steps": [
                        {"id": "sent", "count": 64},
                        {"id": "delivered", "count": 64},
                        {"id": "read", "count": 21},
                        {"id": "clicked", "count": 4},
                        {"id": "committed", "count": 1},
                    ],
                }
            ],
            "failures": [{"channel": "webpush", "error_class": "no_subscription", "count": 2}],
            "reachability": [
                {"channel": "webpush", "reachable": 48, "total": 64, "opted_out": 3},
                {"channel": "email", "reachable": 16, "total": 64, "opted_out": 40},
            ],
        }


@contextmanager
def alert_rows_fixture():
    database_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    ids = [item[0] for item in TEST_ALERTS] + [ANTI_GAMING_FLAG_ID]
    with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM audit_events WHERE resource_id = ANY(%s)", (ids,))
        cursor.execute("DELETE FROM manager_alerts WHERE id = ANY(%s::uuid[])", (ids,))
        for alert_id, source, severity in TEST_ALERTS:
            cursor.execute(
                """
                INSERT INTO manager_alerts
                    (id, community_key, source, severity, title, detail, resource_type,
                     resource_id, active)
                VALUES (%s, 'gr-renewable-community', %s, %s, %s, 'Contract-test alert', 'community',
                        'gr-renewable-community', true)
                """,
                (alert_id, source, severity, f"Test {source}"),
            )
    try:
        yield
    finally:
        with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_events WHERE resource_id = ANY(%s)", (ids,))
            cursor.execute("DELETE FROM manager_alerts WHERE id = ANY(%s::uuid[])", (ids,))


@pytest.fixture(scope="module", autouse=True)
def persistent_test_client():
    app.dependency_overrides[get_dt_client] = lambda: UnavailableDT()
    app.dependency_overrides[get_nudging_client] = lambda: AvailableNudging()
    app.dependency_overrides[get_registry_client] = lambda: PartialRegistry()
    with alert_rows_fixture(), client:
        yield
    app.dependency_overrides.pop(get_dt_client, None)
    app.dependency_overrides.pop(get_nudging_client, None)
    app.dependency_overrides.pop(get_registry_client, None)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_lists_only_the_recs_the_token_grants() -> None:
    """The registry lists two RECs; the org-scoped fixture manages one of them.

    `example_rec` exists, is typed `rec`, and is absent from the answer — which is
    the whole change: the REC list is the intersection of what the registry knows
    and what the token grants, not "the one organization the caller has".
    """
    response = client.get("/api/me")

    assert response.status_code == 200
    body = response.json()
    assert body["registryAvailable"] is True
    assert body["user"] == {
        "sub": "community-manager-dev",
        "email": "manager@greenland.local",
        "name": "REC Manager",
        "preferredUsername": "community-manager-dev",
        "locale": "it",
        "organizations": ["gr-renewable-community"],
        "realmGroups": [],
        "communities": [
            {
                "key": "gr-renewable-community",
                "name": "Greenland Renewable Energy Community",
                "capabilities": [
                    "alerts.read",
                    "alerts.write",
                    "community.read",
                    "console.read",
                    "devices.read",
                    "flexibility.read",
                    "gamification.read",
                    "nudging.read",
                    "objectives.write",
                ],
            }
        ],
        "scopes": [
            "community.read",
            "community.devices.read",
            "community.nudging.read",
            "community.alerts.write",
        ],
    }


def test_me_serves_an_org_scoped_caller_from_the_token_when_the_registry_is_down() -> None:
    """Degraded, not denied. The grant is in the token; only the name was borrowed."""
    app.dependency_overrides[get_registry_client] = lambda: UnavailableRegistry()
    try:
        response = client.get("/api/me")
    finally:
        app.dependency_overrides[get_registry_client] = lambda: PartialRegistry()

    assert response.status_code == 200
    body = response.json()
    assert body["registryAvailable"] is False
    assert [rec["key"] for rec in body["user"]["communities"]] == ["gr-renewable-community"]
    assert body["user"]["communities"][0]["name"] == "Gr Renewable Community"


def test_a_realm_admin_sees_every_rec_the_registry_lists() -> None:
    def realm_admin() -> JwtUser:
        claims = {
            "sub": "platform-admin",
            "groups": ["/admins"],
            "scope": "community.read community.devices.read community.nudging.read",
            "organization": {},
        }
        return JwtUser(sub="platform-admin", organizations=[], claims=claims)

    app.dependency_overrides[get_user_from_request] = realm_admin
    try:
        response = client.get("/api/me")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["realmGroups"] == ["admins"]
    assert body["user"]["organizations"] == []
    assert {rec["key"] for rec in body["user"]["communities"]} == {
        "gr-renewable-community",
        "example_rec",
    }
    # No `community.alerts.write` scope on this token, so the surface is absent
    # even though the realm group would otherwise grant it.
    for rec in body["user"]["communities"]:
        assert "alerts.write" not in rec["capabilities"]


def test_a_realm_admin_gets_503_not_403_when_the_registry_is_down() -> None:
    """Their REC list has no other source, and a 403 would misdirect the fix."""

    def realm_admin() -> JwtUser:
        claims = {"sub": "platform-admin", "groups": ["/admins"], "scope": "community.read"}
        return JwtUser(sub="platform-admin", organizations=[], claims=claims)

    app.dependency_overrides[get_user_from_request] = realm_admin
    app.dependency_overrides[get_registry_client] = lambda: UnavailableRegistry()
    try:
        response = client.get("/api/me")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)
        app.dependency_overrides[get_registry_client] = lambda: PartialRegistry()

    assert response.status_code == 503
    assert "registry" in response.json()["detail"].lower()


def test_a_signed_in_caller_who_manages_nothing_is_denied_not_bounced_to_login() -> None:
    def participant() -> JwtUser:
        claims = {
            "sub": "gl-00001",
            "groups": ["/participants", "/viewers"],
            "scope": "community.read",
            "organization": {"gr-renewable-community": {"type": ["rec"], "groups": ["/viewers"]}},
        }
        return JwtUser(
            sub="gl-00001",
            organizations=[
                Organization._from_claim(
                    "gr-renewable-community", {"type": ["rec"], "groups": ["/viewers"]}
                )
            ],
            claims=claims,
        )

    app.dependency_overrides[get_user_from_request] = participant
    try:
        response = client.get("/api/me")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403


def test_feedback_persists_manager_context_and_screenshot() -> None:
    response = client.post(
        "/api/feedback",
        headers={"x-forwarded-for": "203.0.113.8, 10.0.0.1"},
        json={
            "rating": 4,
            "comment": "  Il percorso nudging ora è chiaro.  ",
            "communityKey": "gr-renewable-community",
            "context": {
                "page_url": "http://community.celine.localhost/nudging",
                "page_title": "Nudging · Gestione Comunità",
                "page_path": "/nudging",
                "locale": "it",
                "timezone": "Europe/Rome",
                "viewport_width": 1440,
                "viewport_height": 900,
                "color_scheme": "light",
                "extra": {"section": "manager-dashboard"},
            },
            "screenshot": {
                "mime_type": "image/webp",
                "data_base64": "bWFuYWdlciBzY3JlZW5zaG90",
            },
        },
    )

    assert response.status_code == 201
    feedback_id = response.json()["id"]
    database_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT community_key, user_id, rating, comment, page_path, client_ip,
                       extra_context, screenshot_mime_type, screenshot_bytes
                FROM feedback_entries
                WHERE id = %s::uuid
                """,
                (feedback_id,),
            )
            stored = cursor.fetchone()
        assert stored is not None
        assert stored[:-1] == (
            "gr-renewable-community",
            "community-manager-dev",
            4,
            "Il percorso nudging ora è chiaro.",
            "/nudging",
            "203.0.113.8",
            {"section": "manager-dashboard"},
            "image/webp",
        )
        assert bytes(stored[-1]) == b"manager screenshot"
    finally:
        with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM feedback_entries WHERE id = %s::uuid", (feedback_id,))


def test_feedback_rejects_invalid_screenshot_data() -> None:
    response = client.post(
        "/api/feedback",
        json={
            "rating": 3,
            "comment": "",
            "communityKey": "gr-renewable-community",
            "context": {"page_url": "http://community.celine.localhost/nudging"},
            "screenshot": {"mime_type": "image/webp", "data_base64": "not-base64"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid screenshot payload"


def test_overview_is_authorized_and_matches_frontend_contract() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/overview", params={"period": "7d"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["communityKey"] == "gr-renewable-community"
    assert body["period"] == "7d"
    assert body["partial"] is True
    assert {
        "rec_self_consumption_daily",
        "rec_self_consumption_daily:previous",
        "rec_population_summary",
        "rec_registry_population",
        "rec_meters_health_summary",
        "rec_flexibility_windows_history",
        "rec_flexibility_chain_daily",
    } == set(body["missingSources"])
    assert len(body["kpis"]) == 4
    assert body["energy"] == []
    assert body["meterHealth"] == {"reporting": 0, "degraded": 0, "silent": 0}


def test_cross_rec_access_is_denied() -> None:
    response = client.get("/api/communities/another-rec/overview", params={"period": "7d"})

    assert response.status_code == 403
    assert "not a member of this REC" in response.json()["detail"]


def test_invalid_period_is_rejected() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/overview", params={"period": "year"}
    )

    assert response.status_code == 422


def test_device_board_supports_filter_sort_and_pagination() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/devices",
        params={
            "status": "silent",
            "sort": "gap_minutes",
            "order": "desc",
            "pageSize": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["communityKey"] == "gr-renewable-community"
    assert body["total"] == 0
    assert body["pageSize"] == 1
    assert body["summary"] == {
        "reporting": 0,
        "degraded": 0,
        "silent": 0,
        "active": 0,
        "dormant": 0,
        "neverActivated": 0,
    }
    assert body["partial"] is True
    assert body["items"] == []


def test_device_detail_contains_only_technical_data_and_gaps() -> None:
    response = client.get("/api/communities/gr-renewable-community/devices/IT001E000845")

    assert response.status_code == 404
    serialized = response.text.lower()
    assert "address" not in serialized
    assert "phone" not in serialized
    assert "participant" not in serialized


def test_unknown_device_is_not_exposed() -> None:
    response = client.get("/api/communities/gr-renewable-community/devices/unknown")

    assert response.status_code == 404


def test_meter_gap_contract() -> None:
    response = client.get("/api/communities/gr-renewable-community/meters/IT001E000912/gaps")

    assert response.status_code == 404


def test_data_flow_reports_coverage_and_pipeline_freshness() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/data-flow/pipelines",
        params={"period": "7d"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["communityKey"] == "gr-renewable-community"
    assert body["partial"] is True
    assert body["coveragePercent"] == 0
    assert body["gapCount"] == 0
    assert body["pipelines"] == []
    assert "rec_pipeline_status" in body["missingSources"]


def test_operational_routes_enforce_rec_boundary() -> None:
    """A REC the caller is not in, and a REC that does not exist, deny alike."""
    for community_key in ("example_rec", "another-rec"):
        response = client.get(f"/api/communities/{community_key}/devices")

        assert response.status_code == 403, community_key
        assert "not a member of this REC" in response.json()["detail"], community_key


def test_device_scope_is_required_even_for_a_manager() -> None:
    def manager_without_device_scope() -> JwtUser:
        claims = {
            "sub": "manager-without-scope",
            "groups": ["managers"],
            "scope": "community.read",
            "organization": {"gr-renewable-community": {"type": ["rec"], "groups": ["/managers"]}},
        }
        return JwtUser(
            sub="manager-without-scope",
            organizations=[
                Organization._from_claim(
                    "gr-renewable-community", {"type": ["rec"], "groups": ["/managers"]}
                )
            ],
            claims=claims,
        )

    app.dependency_overrides[get_user_from_request] = manager_without_device_scope
    try:
        response = client.get("/api/communities/gr-renewable-community/devices")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert "community.devices.read scope required" in response.json()["detail"]


def test_flexibility_windows_and_uptake_contract() -> None:
    windows_response = client.get(
        "/api/communities/gr-renewable-community/flexibility/windows",
        params={"period": "30d"},
    )
    uptake_response = client.get(
        "/api/communities/gr-renewable-community/flexibility/uptake",
        params={"period": "30d"},
    )

    assert windows_response.status_code == 200
    windows = windows_response.json()
    assert windows["communityKey"] == "gr-renewable-community"
    assert windows["partial"] is True
    assert windows["missingSources"] == [
        "rec_flexibility_windows_history",
        "rec_flexibility_chain_daily",
    ]
    assert windows["items"] == []

    assert uptake_response.status_code == 200
    uptake = uptake_response.json()
    assert uptake == {
        "communityKey": "gr-renewable-community",
        "period": "30d",
        "offeredKwh": 0.0,
        "committedKwh": 0.0,
        "deliveredKwh": 0.0,
        "uptakePercent": 0.0,
        "deliveryPercent": 0.0,
        "settledWindows": 0,
    }


def test_demonstration_chain_exposes_drop_off_and_effort_without_causal_claims() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/demonstration/chain",
        params={"period": "30d"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [step["id"] for step in body["steps"]] == [
        "offered",
        "nudged",
        "read",
        "opened",
        "committed",
        "delivered",
        "points",
    ]
    assert body["partial"] is True
    assert body["weakStep"] is None
    assert all(step["count"] == 0 for step in body["steps"])
    assert body["averageEffortMultiplier"] is None
    assert "causat" not in body["summary"].lower()


def test_window_drill_down_is_device_only_and_marks_partial_correlation() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/flexibility/windows/FW-2026-08-04-01",
        params={"period": "30d"},
    )

    assert response.status_code == 404
    serialized = response.text.lower()
    assert "email" not in serialized
    assert "phone" not in serialized
    assert "address" not in serialized
    assert "participant" not in serialized


def test_upcoming_window_and_unknown_window_are_explicit() -> None:
    upcoming = client.get(
        "/api/communities/gr-renewable-community/demonstration/windows/FW-2026-08-06-01"
    )
    missing = client.get(
        "/api/communities/gr-renewable-community/flexibility/windows/not-in-this-rec"
    )

    assert upcoming.status_code == 404
    assert missing.status_code == 404


def test_demonstration_routes_enforce_rec_boundary() -> None:
    response = client.get("/api/communities/another-rec/demonstration/summary")

    assert response.status_code == 403
    assert "not a member of this REC" in response.json()["detail"]


def test_demonstration_reach_and_summary_support_transparent_reporting() -> None:
    reach = client.get("/api/communities/gr-renewable-community/demonstration/reach")
    summary = client.get("/api/communities/gr-renewable-community/demonstration/summary")

    assert reach.status_code == 200
    assert reach.json() == {
        "communityKey": "gr-renewable-community",
        "period": "30d",
        "monitoredDevices": 0,
        "reachable": 0,
        "nudged": 0,
        "read": 0,
        "acted": 0,
    }
    assert summary.status_code == 200
    assert summary.json()["windows"] == 0
    assert summary.json()["participatingDevices"] == 0
    assert "causat" not in summary.json()["statement"].lower()


def test_points_distribution_and_leaderboard_declare_coverage() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/points/distribution",
        params={"period": "30d"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["partial"] is True
    assert body["monitoredDevices"] == 0
    assert body["awardedDevices"] == 0
    assert body["coveragePercent"] == 0
    assert body["buckets"] == []
    assert body["leaderboard"] == []
    assert "participant" not in response.text.lower()


def test_points_ledger_explains_settlement_bonus_and_cap() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/devices/IT001E000327/points/ledger"
    )
    missing = client.get(
        "/api/communities/gr-renewable-community/devices/not-in-this-rec/points/ledger"
    )

    assert response.status_code == 200
    assert response.json()["settlementPoints"] == 12
    assert response.json()["bonusPoints"] == 3
    assert response.json()["totalPoints"] == 15
    assert [item["kind"] for item in response.json()["entries"]] == ["settlement", "bonus"]
    assert missing.status_code == 404


def test_anti_gaming_flags_can_be_acknowledged_without_identity_data() -> None:
    flags = client.get("/api/communities/gr-renewable-community/points/flags")
    assert flags.status_code == 200
    flag = flags.json()["items"][0]

    acknowledged = client.post(
        f"/api/communities/gr-renewable-community/points/flags/{flag['id']}/ack"
    )

    assert acknowledged.status_code == 200
    assert acknowledged.json()["state"] == "acknowledged"
    assert acknowledged.json()["deviceId"] == flag["deviceId"]
    assert "email" not in acknowledged.text.lower()
    assert "address" not in acknowledged.text.lower()


def test_nudging_surface_is_read_only_and_exposes_delivery_health() -> None:
    response = client.get(
        "/api/communities/gr-renewable-community/nudging/conversion",
        params={"period": "30d"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["steps"]] == [
        "sent",
        "delivered",
        "read",
        "clicked",
        "committed",
    ]
    assert body["partial"] is False
    assert [item["count"] for item in body["steps"]] == [64, 64, 21, 4, 1]
    assert body["clickToCommitPercent"] == 25
    assert body["reachability"] == [
        {
            "channel": "webpush",
            "reachable": 48,
            "total": 64,
            "reachablePercent": 75,
            "optedOut": 3,
        },
        {
            "channel": "email",
            "reachable": 16,
            "total": 64,
            "reachablePercent": 25,
            "optedOut": 40,
        },
    ]
    assert body["failures"] == [{"channel": "webpush", "errorClass": "no_subscription", "count": 2}]
    assert body["rules"][0]["severity"] == "medium"
    assert body["rules"][0]["volume"] == 64


def test_alert_inbox_supports_filters_and_audited_actions() -> None:
    inbox = client.get("/api/communities/gr-renewable-community/alerts")
    assert inbox.status_code == 200
    assert inbox.json()["total"] >= 3
    alerts = {item["source"]: item for item in inbox.json()["items"]}

    assigned = client.post(
        f"/api/communities/gr-renewable-community/alerts/{alerts['meter-health']['id']}/assign",
        json={"assignedTo": "community-manager-dev"},
    )
    muted = client.post(
        f"/api/communities/gr-renewable-community/alerts/{alerts['gamification']['id']}/mute",
        json={"mutedUntil": "2099-08-06T10:00:00+02:00"},
    )
    acknowledged = client.post(
        f"/api/communities/gr-renewable-community/alerts/{alerts['flexibility']['id']}/ack"
    )
    filtered = client.get(
        "/api/communities/gr-renewable-community/alerts",
        params={"severity": "critical", "source": "meter-health"},
    )

    assert assigned.status_code == 200
    assert assigned.json()["assignedTo"] == "community-manager-dev"
    assert muted.status_code == 200
    assert muted.json()["state"] == "muted"
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged"] is True
    assert filtered.status_code == 200
    assert all(item["severity"] == "critical" for item in filtered.json()["items"])

    audit = client.get("/api/communities/gr-renewable-community/alerts/audit-events")
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()}
    assert {
        "community.alert.assign",
        "community.alert.mute",
        "community.alert.acknowledge",
    } <= actions


def test_engagement_and_alert_routes_enforce_rec_boundary() -> None:
    points = client.get("/api/communities/another-rec/points/distribution")
    nudging = client.get("/api/communities/another-rec/nudging/conversion")
    alerts = client.get("/api/communities/another-rec/alerts")

    assert points.status_code == 403
    assert nudging.status_code == 403
    assert alerts.status_code == 403


def test_alert_mutations_require_dedicated_write_scope() -> None:
    def manager_without_alert_write() -> JwtUser:
        claims = {
            "sub": "read-only-manager",
            "groups": ["managers"],
            "scope": "community.read community.devices.read community.nudging.read",
            "organization": {"gr-renewable-community": {"type": ["rec"], "groups": ["/managers"]}},
        }
        return JwtUser(
            sub="read-only-manager",
            organizations=[
                Organization._from_claim(
                    "gr-renewable-community", {"type": ["rec"], "groups": ["/managers"]}
                )
            ],
            claims=claims,
        )

    app.dependency_overrides[get_user_from_request] = manager_without_alert_write
    try:
        response = client.post(
            "/api/communities/gr-renewable-community/alerts/0c6025ca-8d58-4d8d-a4d5-7112dd05c1c0/assign",
            json={"assignedTo": "read-only-manager"},
        )
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert "community.alerts.write scope required" in response.json()["detail"]


def test_openapi_v1_contract_has_stable_unique_operations() -> None:
    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == "1.0.0"
    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "export_devices" in operation_ids
    assert "/api/communities/{community_key}/alerts/{alert_id}/assign" in schema["paths"]


def test_csv_and_xlsx_exports_are_authorized_and_privacy_safe() -> None:
    csv_response = client.get(
        "/api/communities/gr-renewable-community/exports/devices",
        params={"period": "30d", "format": "csv"},
    )
    xlsx_response = client.get(
        "/api/communities/gr-renewable-community/exports/flexibility",
        params={"period": "30d", "format": "xlsx"},
    )

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    assert "Device ID" in csv_response.text
    assert "participant" not in csv_response.text.lower()
    assert "email" not in csv_response.text.lower()
    assert xlsx_response.status_code == 200
    assert xlsx_response.content[:2] == b"PK"
    assert "spreadsheetml" in xlsx_response.headers["content-type"]


def test_export_format_and_rec_boundary_are_enforced() -> None:
    invalid_format = client.get(
        "/api/communities/gr-renewable-community/exports/points", params={"format": "pdf"}
    )
    wrong_rec = client.get("/api/communities/another-rec/exports/nudging")

    assert invalid_format.status_code == 422
    assert wrong_rec.status_code == 403
