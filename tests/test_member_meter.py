"""Attach and detach a member's meter: the BFF half of a press (ADR-0003, ADR-0004).

`respx` stands in front of the **real** `RecRegistryAdminClient` and the **real**
`OidcClientCredentialsProvider`s, so the token requests, the paths and the bodies
are the ones production sends. The token endpoint answers by scope, which is how
the tests see which token each registry call carried. The database is a recording
session: what is asserted is the row this module adds.
"""

import json
import logging

import httpx
import pytest
import respx
from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import Organization
from fastapi.testclient import TestClient

from celine.community.api import deps
from celine.community.api.deps import get_user_from_request
from celine.community.api.member_emails import AUDIT_ACTIONS as EMAIL_ACTIONS
from celine.community.api.member_meter import AUDIT_ACTIONS as METER_ACTIONS
from celine.community.db import get_db
from celine.community.db.models import AuditEvent
from celine.community.main import app
from celine.community.settings import settings

REGISTRY = "http://registry.test"
TOKEN_URL = "http://keycloak.test/token"
REC = "example_rec"
MEMBER = "EX-00001"
SENSOR = "SEN-4471-0093"
MEMBER_URL = f"{REGISTRY}/admin/communities/{REC}/members/{MEMBER}"
METERS_URL = f"{REGISTRY}/admin/communities/{REC}/meters"
ASSET_URL = f"{MEMBER_URL}/assets/meter-{SENSOR}"
METER_PATH = f"/api/communities/{REC}/members/{MEMBER}/meter"

READ_TOKEN = "tok-read"
WRITE_TOKEN = "tok-assets-write"

client = TestClient(app)


class RecordingSession:
    def __init__(self) -> None:
        self.added: list = []
        self.committed = 0

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        pass

    def audit_rows(self) -> list[AuditEvent]:
        return [row for row in self.added if isinstance(row, AuditEvent)]


@pytest.fixture
def session():
    recording = RecordingSession()
    app.dependency_overrides[get_db] = lambda: recording
    try:
        yield recording
    finally:
        app.dependency_overrides.pop(get_db, None)


def _token_for(request: httpx.Request) -> httpx.Response:
    form = dict(httpx.QueryParams(request.read().decode()))
    scope = form.get("scope", "")
    if scope == "rec-registry.assets.write":
        return httpx.Response(200, json={"access_token": WRITE_TOKEN, "expires_in": 300})
    if scope == "rec-registry.read":
        return httpx.Response(200, json={"access_token": READ_TOKEN, "expires_in": 300})
    return httpx.Response(400, json={"error": "invalid_scope"})


@pytest.fixture(autouse=True)
def downstream(monkeypatch, session):
    # The development manager of `example_rec`, whichever test module ran first.
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    monkeypatch.setattr(settings, "rec_registry_url", REGISTRY)
    monkeypatch.setattr(settings, "rec_registry_scope", "rec-registry.read")
    monkeypatch.setattr(settings, "rec_registry_assets_write_scope", "rec-registry.assets.write")
    for provider in (deps.registry_token_provider, deps.registry_assets_write_token_provider):
        monkeypatch.setattr(provider, "_token", None)
        monkeypatch.setattr(provider._discovery, "_config", None)
    # Anything not mocked below raises: a call to the Digital Twin or anywhere else
    # fails the test that made it.
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{settings.oidc.base_url}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(
                200,
                json={
                    "issuer": settings.oidc.base_url,
                    "token_endpoint": TOKEN_URL,
                    "jwks_uri": "http://keycloak.test/certs",
                },
            )
        )
        router.post(TOKEN_URL, name="token").mock(side_effect=_token_for)
        yield router


def member(role: str = "consumer", key: str = MEMBER) -> dict:
    return {
        "id": f"id-{key}",
        "key": key,
        "name": "Anna Rossi",
        "role": role,
        "status": "active",
        "area": "north",
        "user_id": "anna@example.org",
    }


def meter(sensor_id: str = SENSOR, *, owner: str = MEMBER, key: str | None = None, **extra):
    return {
        "id": f"id-{key or sensor_id}",
        "key": key or f"meter-{sensor_id}",
        "name": "Meter",
        "sensor_id": sensor_id,
        "meter_type": "consumption",
        "owner_key": owner,
        "owner_user_id": "anna@example.org",
        **extra,
    }


def stored(sensor_id: str = SENSOR, meter_type: str = "consumption") -> dict:
    """The registry's answer to the asset PUT: its `AssetDetail`, as the asset GET."""
    return {
        "id": "id-asset",
        "key": f"meter-{sensor_id}",
        "asset_type": "meter",
        "name": "Meter",
        "sensor_id": sensor_id,
        "properties": {"name": "Meter", "sensor_id": sensor_id, "meter_type": meter_type},
        "device": {},
        "relationships": {},
        "owner_key": MEMBER,
        "owner_user_id": "anna@example.org",
        "extra": {},
        "created_at": "2026-09-27T10:00:00Z",
        "updated_at": "2026-09-27T10:00:00Z",
    }


def registry(downstream, *, role: str = "consumer", held: list | None = None):
    """The two reads every press makes: the member, and the meters they hold."""
    downstream.get(MEMBER_URL, name="member").mock(
        return_value=httpx.Response(200, json=member(role))
    )
    downstream.get(METERS_URL, name="meters").mock(
        return_value=httpx.Response(200, json={"items": held or [], "next_cursor": None})
    )


def refusal(status: int, code: str | None, sentence: str) -> httpx.Response:
    body: dict = {"detail": sentence}
    if code:
        body["code"] = code
    return httpx.Response(status, json=body)


def attach(sensor_id: str = SENSOR, **body):
    return client.put(METER_PATH, json={"sensorId": sensor_id, **body})


def detach(sensor_id: str = SENSOR):
    return client.request("DELETE", METER_PATH, json={"sensorId": sensor_id})


def token_scopes(downstream) -> list[str]:
    return [
        dict(httpx.QueryParams(call.request.read().decode())).get("scope", "")
        for call in downstream.routes["token"].calls
    ]


# ---------------------------------------------------------------------------
# Which token each call carries
# ---------------------------------------------------------------------------


def test_only_the_write_carries_the_assets_write_scope(downstream) -> None:
    registry(downstream)
    put = downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    assert attach().status_code == 201

    assert sorted(token_scopes(downstream)) == ["rec-registry.assets.write", "rec-registry.read"]
    assert downstream.routes["member"].calls.last.request.headers["authorization"] == (
        f"Bearer {READ_TOKEN}"
    )
    assert downstream.routes["meters"].calls.last.request.headers["authorization"] == (
        f"Bearer {READ_TOKEN}"
    )
    assert put.calls.last.request.headers["authorization"] == f"Bearer {WRITE_TOKEN}"


def test_the_detach_is_the_only_call_with_the_write_token(downstream) -> None:
    registry(downstream, held=[meter()])
    delete = downstream.delete(ASSET_URL).mock(return_value=httpx.Response(204))

    assert detach().status_code == 204

    assert delete.calls.last.request.headers["authorization"] == f"Bearer {WRITE_TOKEN}"
    assert downstream.routes["meters"].calls.last.request.headers["authorization"] == (
        f"Bearer {READ_TOKEN}"
    )


def test_a_press_that_writes_nothing_never_asks_for_the_write_token(downstream) -> None:
    registry(downstream, held=[meter()])

    assert attach().status_code == 200

    assert token_scopes(downstream) == ["rec-registry.read"]


def test_the_dialog_reads_no_meter_data_but_that_members(downstream) -> None:
    """D18: no candidate list, no Digital Twin, no community-wide meter read."""
    registry(downstream, held=[meter()])
    downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    client.get(METER_PATH)
    attach("SEN-OTHER")

    hosts = {call.request.url.host for call in downstream.calls}
    assert hosts <= {"registry.test", "keycloak.test", httpx.URL(settings.oidc.base_url).host}
    for call in downstream.routes["meters"].calls:
        assert call.request.url.params["owner"] == MEMBER


# ---------------------------------------------------------------------------
# Attach
# ---------------------------------------------------------------------------


def test_an_attach_puts_meter_trimmed_id_with_the_trimmed_id(downstream) -> None:
    registry(downstream)
    put = downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    response = attach(f"  {SENSOR}\t")

    assert response.status_code == 201
    assert response.json() == {
        "outcome": "attached",
        "sensorId": SENSOR,
        "meterType": "consumption",
    }
    assert json.loads(put.calls.last.request.read()) == {
        "key": f"meter-{SENSOR}",
        "asset_type": "meter",
        "properties": {"name": "Meter", "sensor_id": SENSOR, "meter_type": "consumption"},
    }


@pytest.mark.parametrize(
    ("role", "chosen", "sent"),
    [
        ("prosumer", None, "bidirectional"),
        ("consumer", None, "consumption"),
        ("producer", None, "consumption"),
        ("prosumer", "export", "export"),
        ("consumer", "production", "production"),
    ],
)
def test_the_meter_type_defaults_from_the_role_and_the_manager_may_change_it(
    downstream, role, chosen, sent
) -> None:
    registry(downstream, role=role)
    put = downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    body = {"meterType": chosen} if chosen else {}
    response = attach(**body)

    assert response.status_code == 201
    assert response.json()["meterType"] == sent
    assert json.loads(put.calls.last.request.read())["properties"]["meter_type"] == sent


def test_a_meter_type_outside_the_registry_vocabulary_is_refused_before_it(downstream) -> None:
    registry(downstream)

    response = attach(meterType="solar")

    assert response.status_code == 422
    assert not downstream.routes["member"].called


def test_the_same_id_again_changes_nothing(downstream, session) -> None:
    registry(downstream, held=[meter(f" {SENSOR} ", meter_type="bidirectional")])
    put = downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    response = attach(SENSOR)

    assert response.status_code == 200
    assert response.json() == {
        "outcome": "already_attached",
        "sensorId": SENSOR,
        "meterType": "bidirectional",
    }
    assert not put.called
    [row] = session.audit_rows()
    assert row.detail == {"code": "already_attached", "status": 200}


def test_a_blank_id_is_refused_before_the_registry_and_writes_no_row(downstream, session) -> None:
    registry(downstream)

    response = attach("   ")

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "sensor_id_blank"}}
    assert not downstream.routes["member"].called
    assert session.added == []


def test_sensor_held_passes_through_naming_no_one(downstream) -> None:
    registry(downstream)
    downstream.put(ASSET_URL).mock(
        return_value=refusal(
            409, "sensor_held", "Sensor held by member EX-00777 in community other_rec"
        )
    )

    response = attach()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "sensor_held"}}
    for leaked in ("EX-00777", "other_rec", "held by"):
        assert leaked not in response.text


def test_asset_key_taken_passes_through(downstream) -> None:
    registry(downstream)
    downstream.put(ASSET_URL).mock(
        return_value=refusal(409, "asset_key_taken", f"Asset 'meter-{SENSOR}' is taken")
    )

    response = attach()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "asset_key_taken"}}


def test_the_key_held_by_this_member_for_another_sensor_is_not_replaced(
    downstream, session, caplog
) -> None:
    # Imported data that breaks the convention: `meter-<SENSOR>` carries another id.
    registry(downstream, held=[meter("SEN-OTHER-0001", key=f"meter-{SENSOR}")])
    put = downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    with caplog.at_level(logging.DEBUG):
        response = attach(f"  {SENSOR} ")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "asset_key_taken"}}
    assert not put.called
    assert "rec-registry.assets.write" not in token_scopes(downstream)
    [row] = session.audit_rows()
    assert row.detail == {"code": "asset_key_taken", "status": 200}
    assert SENSOR not in caplog.text and "SEN-OTHER" not in caplog.text


def test_a_key_held_by_this_member_without_a_sensor_id_is_not_replaced(downstream) -> None:
    registry(downstream, held=[{**meter(), "sensor_id": None}])
    put = downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))

    response = attach()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "asset_key_taken"}}
    assert not put.called


def test_an_id_of_122_characters_is_attached_and_123_is_refused_before_the_registry(
    downstream, session
) -> None:
    longest = "S" * 122
    registry(downstream)
    put = downstream.put(f"{MEMBER_URL}/assets/meter-{longest}").mock(
        return_value=httpx.Response(200, json=stored(longest))
    )

    assert attach(longest).status_code == 201
    assert len(json.loads(put.calls.last.request.content)["key"]) == 128

    response = attach("S" * 123)

    assert response.status_code == 422
    assert downstream.routes["member"].call_count == 1
    assert len(session.audit_rows()) == 1


def test_the_attach_body_declares_the_122_character_ceiling() -> None:
    schema = app.openapi()["components"]["schemas"]["MeterAttach"]
    assert schema["properties"]["sensorId"]["maxLength"] == 122


def test_asset_key_too_long_reaches_the_dashboard_by_code(downstream, caplog) -> None:
    registry(downstream)
    downstream.put(ASSET_URL).mock(
        return_value=refusal(422, "asset_key_too_long", "Asset key is 129 characters; 128 allowed")
    )

    with caplog.at_level(logging.DEBUG):
        response = attach()

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "asset_key_too_long"}}
    assert "129 characters" not in response.text
    assert SENSOR not in caplog.text


def test_a_member_not_in_this_rec_is_404_and_nothing_is_written(downstream, session) -> None:
    downstream.get(MEMBER_URL).mock(
        return_value=refusal(404, "member_not_found", f"Member {MEMBER!r} not found")
    )
    put = downstream.put(ASSET_URL)

    response = attach()

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "member_not_found"}}
    assert not put.called
    [row] = session.audit_rows()
    assert row.detail == {"code": "member_not_found", "status": 404}


@pytest.mark.parametrize(
    "registry_answer",
    [httpx.Response(500, text="boom"), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
)
def test_a_registry_outage_on_the_write_is_a_502_with_a_code(
    downstream, session, registry_answer
) -> None:
    registry(downstream)
    route = downstream.put(ASSET_URL)
    if isinstance(registry_answer, Exception):
        route.mock(side_effect=registry_answer)
    else:
        route.mock(return_value=registry_answer)

    response = attach()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_unavailable"}}
    # Pressed once, sent once: no retry.
    assert route.call_count == 1
    [row] = session.audit_rows()
    assert row.detail["code"] == "registry_unavailable"


def test_a_registry_outage_on_the_read_is_a_502_with_a_code(downstream) -> None:
    downstream.get(MEMBER_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = attach()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_unavailable"}}


def test_a_missing_registry_grant_is_a_deployment_fault_not_a_403(downstream, caplog) -> None:
    registry(downstream)
    downstream.put(ASSET_URL).mock(return_value=httpx.Response(403, json={"detail": "Forbidden"}))

    response = attach()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_refused"}}


def test_a_realm_without_the_optional_scope_is_registry_refused(
    downstream, monkeypatch, caplog
) -> None:
    registry(downstream)
    put = downstream.put(ASSET_URL)
    monkeypatch.setattr(
        deps.registry_assets_write_token_provider, "_scope", "rec-registry.assets.nope"
    )

    with caplog.at_level(logging.ERROR):
        response = attach()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_refused"}}
    assert not put.called
    assert any("rec-registry.assets.write" in r.getMessage() for r in caplog.records)


def test_without_the_write_scope_configured_nothing_is_pressed(
    downstream, monkeypatch, session
) -> None:
    monkeypatch.setattr(settings, "rec_registry_assets_write_scope", "")
    registry(downstream)

    response = attach()

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "meter_writes_not_configured"}}
    assert not downstream.routes["member"].called
    assert session.added == []


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


def test_a_detach_deletes_that_one_meter_and_leaves_the_others(downstream, session) -> None:
    registry(downstream, held=[meter(), meter("SEN-SECOND")])
    delete = downstream.delete(ASSET_URL).mock(return_value=httpx.Response(204))
    other = downstream.delete(f"{MEMBER_URL}/assets/meter-SEN-SECOND")

    response = detach(f" {SENSOR} ")

    assert response.status_code == 204
    assert delete.call_count == 1
    assert not other.called
    [row] = session.audit_rows()
    assert row.action == "community.member.meter.detach"
    assert row.detail == {"code": "detached", "status": 204}


def test_a_meter_held_under_another_key_is_detached_by_that_key(downstream) -> None:
    """An imported meter need not be at `meter-<id>`; nothing here special-cases it."""
    registry(downstream, held=[meter(key="ex-00001-m1")])
    delete = downstream.delete(f"{MEMBER_URL}/assets/ex-00001-m1").mock(
        return_value=httpx.Response(204)
    )

    assert detach().status_code == 204
    assert delete.called


def test_detaching_a_meter_the_member_does_not_hold_is_404(downstream, session) -> None:
    registry(downstream, held=[meter("SEN-SECOND")])
    delete = downstream.delete(ASSET_URL)

    response = detach()

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "meter_not_found"}}
    assert not delete.called
    [row] = session.audit_rows()
    assert row.detail == {"code": "meter_not_found", "status": 200}


def test_a_meter_gone_between_the_read_and_the_delete_is_404(downstream) -> None:
    registry(downstream, held=[meter()])
    downstream.delete(ASSET_URL).mock(
        return_value=refusal(404, "asset_not_found", f"Asset 'meter-{SENSOR}' not found")
    )

    response = detach()

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "meter_not_found"}}


def test_a_detach_for_a_member_not_in_this_rec_is_404(downstream) -> None:
    downstream.get(MEMBER_URL).mock(
        return_value=refusal(404, "member_not_found", f"Member {MEMBER!r} not found")
    )

    response = detach()

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "member_not_found"}}


def test_a_registry_outage_on_detach_is_a_502(downstream) -> None:
    registry(downstream, held=[meter()])
    downstream.delete(ASSET_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = detach()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_unavailable"}}


# ---------------------------------------------------------------------------
# The dialog's read
# ---------------------------------------------------------------------------


def test_the_dialog_shows_that_members_meters_and_the_default_type(downstream) -> None:
    registry(
        downstream,
        role="prosumer",
        held=[
            meter(meter_type="bidirectional"),
            # A registry that ignored the owner filter must not show this one.
            meter("SEN-NOT-THEIRS", owner="EX-00002"),
        ],
    )

    response = client.get(METER_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "memberKey": MEMBER,
        "defaultMeterType": "bidirectional",
        "meters": [{"sensorId": SENSOR, "meterType": "bidirectional"}],
    }
    assert downstream.routes["meters"].calls.last.request.url.params["owner"] == MEMBER


def test_the_dialog_for_a_member_not_in_this_rec_is_404(downstream) -> None:
    downstream.get(MEMBER_URL).mock(return_value=refusal(404, "member_not_found", "not found"))

    response = client.get(METER_PATH)

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "member_not_found"}}


# ---------------------------------------------------------------------------
# Who may press
# ---------------------------------------------------------------------------


def _as(caller: JwtUser) -> None:
    app.dependency_overrides[get_user_from_request] = lambda: caller


def _manager_of(alias: str) -> JwtUser:
    orgs = {alias: {"type": ["rec"], "groups": ["/managers"]}}
    return JwtUser(
        sub=f"manager-of-{alias}",
        organizations=[Organization._from_claim(alias, orgs[alias])],
        claims={"sub": f"manager-of-{alias}", "scope": "", "organization": orgs},
    )


def _service(scope: str) -> JwtUser:
    claims = {"sub": "svc", "preferred_username": "service-account-svc-x", "scope": scope}
    return JwtUser(sub="svc", preferred_username="service-account-svc-x", claims=claims)


@pytest.mark.parametrize(
    "caller",
    [
        _manager_of("other_rec"),
        _service("community.admin community.read"),
        JwtUser(
            sub="viewer",
            organizations=[
                Organization._from_claim(REC, {"type": ["rec"], "groups": ["/viewers"]})
            ],
            claims={
                "sub": "viewer",
                "scope": "",
                "organization": {REC: {"type": ["rec"], "groups": ["/viewers"]}},
            },
        ),
        JwtUser(sub="realm-manager", claims={"sub": "realm-manager", "groups": ["/managers"]}),
    ],
    ids=["manager-of-another-rec", "service-community-admin", "viewer", "realm-managers"],
)
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_a_caller_without_members_meter_is_refused_before_the_registry(
    downstream, session, caller, method
) -> None:
    registry(downstream)
    _as(caller)
    try:
        kwargs = {} if method == "GET" else {"json": {"sensorId": SENSOR}}
        response = client.request(method, METER_PATH, **kwargs)
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not downstream.routes["member"].called
    assert session.added == []


def test_a_realm_admin_may_attach_on_any_rec(downstream) -> None:
    registry(downstream)
    downstream.put(ASSET_URL).mock(return_value=httpx.Response(200, json=stored()))
    _as(JwtUser(sub="platform-admin", claims={"sub": "platform-admin", "groups": ["/admins"]}))
    try:
        response = attach()
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# The audit row and the log: never the sensor id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("press", "put_answer", "code", "status"),
    [
        ("attach", httpx.Response(200, json=stored()), "attached", 200),
        ("attach", refusal(409, "sensor_held", f"Sensor {SENSOR!r} is held"), "sensor_held", 409),
        ("attach", httpx.Response(500, text=f"trace with {SENSOR}"), "registry_unavailable", 500),
        ("detach", httpx.Response(204), "detached", 204),
        ("detach", refusal(404, "asset_not_found", f"meter-{SENSOR}"), "meter_not_found", 404),
    ],
)
def test_one_row_per_press_and_no_sensor_id_in_it_or_the_log(
    downstream, session, caplog, press, put_answer, code, status
) -> None:
    registry(downstream, held=[meter()] if press == "detach" else [])
    if press == "attach":
        downstream.put(ASSET_URL).mock(return_value=put_answer)
    else:
        downstream.delete(ASSET_URL).mock(return_value=put_answer)

    with caplog.at_level(logging.DEBUG):
        attach() if press == "attach" else detach()

    [row] = session.audit_rows()
    assert session.committed == 1
    assert row.community_key == REC
    assert row.actor_id == settings.dev_user_sub
    assert row.action == f"community.member.meter.{press}"
    assert row.resource_type == "registry_member"
    assert row.resource_id == MEMBER
    assert row.detail == {"code": code, "status": status}
    written = json.dumps(
        [
            row.community_key,
            row.actor_id,
            row.action,
            row.resource_type,
            row.resource_id,
            row.detail,
        ]
    )
    assert SENSOR not in written

    logged = "\n".join(record.getMessage() for record in caplog.records)
    # httpx logs every request URL, and the registry's asset path carries the id.
    assert any("HTTP Request" in record.getMessage() for record in caplog.records)
    assert SENSOR not in logged
    assert "Anna" not in logged and "anna@example.org" not in logged


def test_the_sent_emails_view_does_not_list_meter_presses() -> None:
    """`…/members/sends` reads exactly the email actions, and these are not among them."""
    assert set(METER_ACTIONS.values()).isdisjoint(EMAIL_ACTIONS.values())
