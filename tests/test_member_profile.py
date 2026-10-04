"""Correct a member's role and area, and read the REC's areas (ADR-0003, ADR-0004).

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
from celine.community.api.member_profile import AUDIT_ACTION as PROFILE_ACTION
from celine.community.db import get_db
from celine.community.db.models import AuditEvent
from celine.community.main import app
from celine.community.settings import settings

REGISTRY = "http://registry.test"
TOKEN_URL = "http://keycloak.test/token"
REC = "example_rec"
MEMBER = "EX-00001"
COMMUNITY_URL = f"{REGISTRY}/admin/communities/{REC}"
MEMBER_URL = f"{COMMUNITY_URL}/members/{MEMBER}"
PROFILE_URL = f"{MEMBER_URL}/profile"
EDIT_PATH = f"/api/communities/{REC}/members/{MEMBER}"
AREAS_PATH = f"/api/communities/{REC}/areas"

READ_TOKEN = "tok-read"
WRITE_TOKEN = "tok-profile-write"
PROFILE_SCOPE = "rec-registry.members.profile.write"

NAME = "Anna Rossi"
EMAIL = "anna@example.org"

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
    if scope == PROFILE_SCOPE:
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
    monkeypatch.setattr(settings, "rec_registry_profile_write_scope", PROFILE_SCOPE)
    for provider in (deps.registry_token_provider, deps.registry_profile_write_token_provider):
        monkeypatch.setattr(provider, "_token", None)
        monkeypatch.setattr(provider._discovery, "_config", None)
    # Anything not mocked below raises: a call anywhere else fails the test that made it.
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
        # The general member PATCH needs `members.write`: never called from here.
        router.patch(MEMBER_URL, name="general_patch").mock(
            return_value=httpx.Response(500, json={"detail": "not this route"})
        )
        yield router


def member(role: str = "consumer", area: str = "north", status: str = "active") -> dict:
    return {
        "id": f"id-{MEMBER}",
        "key": MEMBER,
        "name": NAME,
        "role": role,
        "status": status,
        "area": area,
        "user_id": EMAIL,
    }


def community(areas: dict | None = None) -> dict:
    return {
        "id": "id-rec",
        "key": REC,
        "name": "Example REC",
        "areas": areas
        if areas is not None
        else {"north": {"name": "North"}, "south": {"name": "South"}},
        "topology": [],
    }


def registry(
    downstream, *, role: str = "consumer", area: str = "north", areas=None, status="active"
):
    downstream.get(MEMBER_URL, name="member").mock(
        return_value=httpx.Response(200, json=member(role, area, status))
    )
    downstream.get(COMMUNITY_URL, name="community").mock(
        return_value=httpx.Response(200, json=community(areas))
    )


def written(downstream, *, role: str | None = None, area: str | None = None):
    """The registry's profile route, answering the member as it now stands."""

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json=member(body.get("role", role or "consumer"), body.get("area", area or "north")),
        )

    return downstream.patch(PROFILE_URL, name="profile").mock(side_effect=answer)


def refusal(status: int, code: str | None, sentence: str) -> httpx.Response:
    body: dict = {"detail": sentence}
    if code:
        body["code"] = code
    return httpx.Response(status, json=body)


def edit(**body):
    return client.patch(EDIT_PATH, json=body)


def token_scopes(downstream) -> list[str]:
    return [
        dict(httpx.QueryParams(call.request.read().decode())).get("scope", "")
        for call in downstream.routes["token"].calls
    ]


def sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# Which token each call carries, and what reaches the registry
# ---------------------------------------------------------------------------


def test_only_the_profile_write_carries_the_profile_scope(downstream) -> None:
    registry(downstream)
    profile = written(downstream)

    response = edit(role="prosumer", area="south")

    assert response.status_code == 200, response.text
    assert sorted(token_scopes(downstream)) == [PROFILE_SCOPE, "rec-registry.read"]
    for read in ("member", "community"):
        assert downstream.routes[read].calls.last.request.headers["authorization"] == (
            f"Bearer {READ_TOKEN}"
        )
    assert profile.calls.last.request.headers["authorization"] == f"Bearer {WRITE_TOKEN}"
    assert not downstream.routes["general_patch"].called


def test_a_role_change_sends_the_role_alone_to_the_profile_route(downstream) -> None:
    registry(downstream)
    profile = written(downstream)

    response = edit(role="prosumer")

    assert response.status_code == 200
    assert response.json() == {
        "outcome": "updated",
        "memberKey": MEMBER,
        "role": "prosumer",
        "area": "north",
        "changed": ["role"],
    }
    assert sent(profile) == {"role": "prosumer"}
    # A role change needs no area list.
    assert not downstream.routes["community"].called


def test_an_area_change_is_checked_against_the_recs_areas_and_sent_alone(downstream) -> None:
    registry(downstream)
    profile = written(downstream)

    response = edit(area="south")

    assert response.status_code == 200
    assert response.json()["changed"] == ["area"]
    assert response.json()["area"] == "south"
    assert sent(profile) == {"area": "south"}
    assert downstream.routes["community"].called


def test_both_are_sent_in_one_call(downstream) -> None:
    registry(downstream)
    profile = written(downstream)

    response = edit(role="prosumer", area="south")

    assert response.json()["changed"] == ["area", "role"]
    assert sent(profile) == {"role": "prosumer", "area": "south"}
    assert profile.call_count == 1


def test_only_what_changes_is_sent(downstream) -> None:
    registry(downstream, role="consumer", area="north")
    profile = written(downstream)

    response = edit(role="consumer", area="south")

    assert response.json()["changed"] == ["area"]
    assert sent(profile) == {"area": "south"}


def test_the_role_is_trimmed_and_lowercased(downstream) -> None:
    registry(downstream)
    profile = written(downstream)

    assert edit(role="  Prosumer ").status_code == 200
    assert sent(profile) == {"role": "prosumer"}


def test_the_answer_is_the_registrys_member_as_it_now_stands(downstream) -> None:
    registry(downstream)
    downstream.patch(PROFILE_URL).mock(
        return_value=httpx.Response(200, json=member("prosumer", "north"))
    )

    response = edit(role="prosumer")

    assert response.json()["role"] == "prosumer"
    assert NAME not in response.text and EMAIL not in response.text


def test_nothing_to_change_writes_nothing_and_asks_for_no_write_token(downstream, session) -> None:
    registry(downstream, role="prosumer", area="south")
    profile = written(downstream)

    response = edit(role="prosumer", area="south")

    assert response.status_code == 200
    assert response.json() == {
        "outcome": "unchanged",
        "memberKey": MEMBER,
        "role": "prosumer",
        "area": "south",
        "changed": [],
    }
    assert not profile.called
    assert PROFILE_SCOPE not in token_scopes(downstream)
    [row] = session.audit_rows()
    assert row.detail == {"code": "unchanged", "status": 200, "changed": []}


# ---------------------------------------------------------------------------
# D30: consumer and prosumer only, refused before any registry write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["producer", "operator", "admin", "owner", ""])
def test_a_role_other_than_consumer_or_prosumer_is_refused_before_the_registry(
    downstream, session, role
) -> None:
    registry(downstream)
    profile = written(downstream)

    response = edit(role=role)

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "role_not_allowed"}}
    assert not downstream.routes["member"].called
    assert not profile.called
    assert not downstream.routes["token"].called
    assert session.added == []


@pytest.mark.parametrize("current", ["producer", "operator", "admin"])
@pytest.mark.parametrize("asked", ["consumer", "prosumer"])
def test_the_role_of_a_member_outside_consumer_and_prosumer_is_read_only(
    downstream, session, current, asked
) -> None:
    registry(downstream, role=current)
    profile = written(downstream)

    response = edit(role=asked, area="south")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "role_read_only"}}
    assert not profile.called
    assert PROFILE_SCOPE not in token_scopes(downstream)
    [row] = session.audit_rows()
    assert row.detail == {
        "code": "role_read_only",
        "status": 200,
        "changed": [],
        "attempted": {
            "role": {"from": current, "to": asked},
            "area": {"from": "north", "to": "south"},
        },
    }


@pytest.mark.parametrize("current", ["producer", "operator", "admin"])
def test_the_area_of_such_a_member_stays_editable(downstream, current) -> None:
    registry(downstream, role=current)
    profile = written(downstream, role=current)

    response = edit(area="south")

    assert response.status_code == 200
    assert response.json()["role"] == current
    assert sent(profile) == {"area": "south"}


@pytest.mark.parametrize("body", [{}, {"role": None}, {"role": None, "area": None}])
def test_an_empty_edit_is_refused_before_the_registry(downstream, session, body) -> None:
    registry(downstream)

    response = client.patch(EDIT_PATH, json=body)

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "profile_empty"}}
    assert not downstream.routes["token"].called
    assert session.added == []


@pytest.mark.parametrize(
    "extra", [{"status": "inactive"}, {"userId": "x"}, {"name": "X"}, {"did": "did:x"}]
)
def test_no_other_member_field_is_accepted(downstream, session, extra) -> None:
    registry(downstream)

    response = client.patch(EDIT_PATH, json={"role": "prosumer", **extra})

    assert response.status_code == 422
    assert not downstream.routes["token"].called
    assert session.added == []


def test_a_blank_area_is_refused_before_the_registry(downstream, session) -> None:
    registry(downstream)

    response = edit(area="   ")

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "unknown_area"}}
    assert not downstream.routes["token"].called
    assert session.added == []


def test_an_area_the_rec_does_not_have_is_refused_without_a_write(downstream, session) -> None:
    registry(downstream)
    profile = written(downstream)

    response = edit(area="elsewhere")

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "unknown_area"}}
    assert not profile.called
    assert PROFILE_SCOPE not in token_scopes(downstream)
    [row] = session.audit_rows()
    assert row.detail["code"] == "unknown_area"


# ---------------------------------------------------------------------------
# Only an active member is edited (ADR-0004); a refusal records what was asked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending", "suspended", "inactive"])
def test_a_member_who_is_not_active_is_not_edited(downstream, session, status) -> None:
    registry(downstream, status=status)
    profile = written(downstream)

    response = edit(role="prosumer", area="south")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "member_not_active"}}
    assert not profile.called
    assert not downstream.routes["community"].called
    assert PROFILE_SCOPE not in token_scopes(downstream)
    [row] = session.audit_rows()
    assert row.detail == {
        "code": "member_not_active",
        "status": 200,
        "changed": [],
        "attempted": {
            "role": {"from": "consumer", "to": "prosumer"},
            "area": {"from": "north", "to": "south"},
        },
    }


def test_a_refused_press_leaves_out_what_it_asked_that_was_already_true(
    downstream, session
) -> None:
    registry(downstream, role="consumer", area="north")

    response = edit(role="consumer", area="elsewhere")

    assert response.status_code == 422
    [row] = session.audit_rows()
    assert row.detail["attempted"] == {"area": {"from": "north", "to": "elsewhere"}}


def test_a_refused_press_on_an_unread_member_lists_every_asked_field(downstream, session) -> None:
    downstream.get(MEMBER_URL).mock(return_value=refusal(404, "member_not_found", "no"))

    response = edit(role="prosumer", area="south")

    assert response.status_code == 404
    [row] = session.audit_rows()
    assert row.detail == {
        "code": "member_not_found",
        "status": 404,
        "changed": [],
        "attempted": {
            "role": {"from": None, "to": "prosumer"},
            "area": {"from": None, "to": "south"},
        },
    }


def test_an_accepted_press_records_no_attempted(downstream, session) -> None:
    registry(downstream)
    written(downstream)

    edit(role="prosumer")

    [row] = session.audit_rows()
    assert "attempted" not in row.detail


# ---------------------------------------------------------------------------
# The registry's answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (422, "invalid_role"),
        (422, "unknown_area"),
        (404, "member_not_found"),
        (404, "community_not_found"),
    ],
)
def test_registry_codes_pass_through_without_the_sentence(
    downstream, session, caplog, status, code
) -> None:
    registry(downstream)
    downstream.patch(PROFILE_URL).mock(
        return_value=refusal(status, code, f"Member {NAME} ({EMAIL}) refused")
    )

    with caplog.at_level(logging.DEBUG):
        response = edit(role="prosumer")

    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert NAME not in caplog.text and EMAIL not in caplog.text
    [row] = session.audit_rows()
    assert row.detail == {
        "code": code,
        "status": status,
        "changed": [],
        "attempted": {"role": {"from": "consumer", "to": "prosumer"}},
    }


def test_an_uncoded_validation_refusal_is_profile_rejected(downstream) -> None:
    registry(downstream)
    downstream.patch(PROFILE_URL).mock(
        return_value=httpx.Response(422, json={"detail": [{"msg": "bad"}]})
    )

    response = edit(role="prosumer")

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "profile_rejected"}}


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (refusal(404, "member_not_found", "no such member"), "member_not_found"),
        (refusal(404, "community_not_found", "no such community"), "community_not_found"),
        (httpx.Response(404, json={"detail": "Not found"}), "member_not_found"),
    ],
)
def test_a_member_not_in_this_rec_is_404_and_nothing_is_written(downstream, answer, code) -> None:
    downstream.get(MEMBER_URL).mock(return_value=answer)
    profile = written(downstream)

    response = edit(role="prosumer")

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": code}}
    assert not profile.called


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(500, text="boom"), httpx.ConnectError("down")],
    ids=["500", "unreachable"],
)
def test_a_registry_outage_on_the_write_is_a_502(downstream, session, answer) -> None:
    registry(downstream)
    route = downstream.patch(PROFILE_URL)
    if isinstance(answer, Exception):
        route.mock(side_effect=answer)
    else:
        route.mock(return_value=answer)

    response = edit(role="prosumer")

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_unavailable"}}
    assert len(session.audit_rows()) == 1


def test_a_registry_outage_on_the_read_is_a_502(downstream) -> None:
    downstream.get(MEMBER_URL).mock(return_value=httpx.Response(503, text="down"))

    response = edit(role="prosumer")

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_unavailable"}}


def test_an_area_list_outage_is_a_502_and_nothing_is_written(downstream) -> None:
    downstream.get(MEMBER_URL).mock(return_value=httpx.Response(200, json=member()))
    downstream.get(COMMUNITY_URL).mock(return_value=httpx.Response(500, text="boom"))
    profile = written(downstream)

    response = edit(area="south")

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_unavailable"}}
    assert not profile.called


def test_a_missing_registry_grant_is_a_deployment_fault_not_a_403(downstream) -> None:
    registry(downstream)
    downstream.patch(PROFILE_URL).mock(return_value=httpx.Response(403, json={"detail": "no"}))

    response = edit(role="prosumer")

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_refused"}}


def test_a_realm_without_the_optional_scope_is_registry_refused(
    downstream, monkeypatch, caplog
) -> None:
    registry(downstream)
    profile = written(downstream)
    monkeypatch.setattr(
        deps.registry_profile_write_token_provider, "_scope", "rec-registry.members.nope"
    )

    with caplog.at_level(logging.ERROR):
        response = edit(role="prosumer")

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "registry_refused"}}
    assert not profile.called
    assert any(PROFILE_SCOPE in r.getMessage() for r in caplog.records)


def test_without_the_profile_scope_configured_nothing_is_pressed(
    downstream, monkeypatch, session
) -> None:
    registry(downstream)
    monkeypatch.setattr(settings, "rec_registry_profile_write_scope", "")

    response = edit(role="prosumer")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "profile_writes_not_configured"}}
    assert not downstream.routes["member"].called
    assert session.added == []


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
        JwtUser(
            sub="legacy-admin",
            claims={
                "sub": "legacy-admin",
                "groups": ["/admins", "admins"],
                "realm_access": {"roles": ["admin"]},
            },
        ),
    ],
    ids=[
        "manager-of-another-rec",
        "service-community-admin",
        "viewer",
        "realm-managers",
        "retired-realm-admins",
    ],
)
def test_a_caller_without_members_edit_is_refused_before_the_registry(
    downstream, session, caller
) -> None:
    registry(downstream)
    _as(caller)
    try:
        response = edit(role="prosumer")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not downstream.routes["member"].called
    assert session.added == []


def test_a_platform_admin_may_edit_on_any_rec(downstream) -> None:
    registry(downstream)
    written(downstream)
    _as(
        JwtUser(
            sub="platform-admin",
            claims={"sub": "platform-admin", "realm_access": {"roles": ["platform-admin"]}},
        )
    )
    try:
        response = edit(role="prosumer")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The audit row and the log
# ---------------------------------------------------------------------------


def test_one_row_per_press_naming_the_key_and_the_change(downstream, session, caplog) -> None:
    registry(downstream, role="consumer", area="north")
    written(downstream)

    with caplog.at_level(logging.DEBUG):
        edit(role="prosumer", area="south")

    [row] = session.audit_rows()
    assert session.committed == 1
    assert row.community_key == REC
    assert row.actor_id == settings.dev_user_sub
    assert row.action == "community.member.profile"
    assert row.resource_type == "registry_member"
    assert row.resource_id == MEMBER
    assert row.detail == {
        "code": "updated",
        "status": 200,
        "changed": ["area", "role"],
        "role": {"from": "consumer", "to": "prosumer"},
        "area": {"from": "north", "to": "south"},
    }
    stored = json.dumps([row.community_key, row.actor_id, row.action, row.resource_id, row.detail])
    assert NAME not in stored and EMAIL not in stored

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert NAME not in logged and EMAIL not in logged


def test_the_sent_emails_view_does_not_list_profile_presses() -> None:
    """`…/members/sends` reads exactly the email actions, and this is not among them."""
    assert PROFILE_ACTION not in EMAIL_ACTIONS.values()


# ---------------------------------------------------------------------------
# The REC's areas, for the dialog
# ---------------------------------------------------------------------------


def test_the_areas_are_listed_by_key_with_their_names(downstream) -> None:
    registry(downstream)

    response = client.get(AREAS_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "communityKey": REC,
        "areas": [
            {"key": "north", "name": "North", "boundary": None, "primarySubstation": None},
            {"key": "south", "name": "South", "boundary": None, "primarySubstation": None},
        ],
    }
    assert token_scopes(downstream) == ["rec-registry.read"]


def test_an_areas_boundary_is_shown_when_the_registry_records_one(downstream) -> None:
    registry(
        downstream,
        areas={
            "north": {
                "name": "North",
                "boundary": {"source": "gse_cabine_primarie", "id": "AC000E00000"},
                "topology": ["AC000E00000"],
            },
            "south": {"name": "South"},
        },
    )

    areas = client.get(AREAS_PATH).json()["areas"]

    assert areas[0]["boundary"] == {"source": "gse_cabine_primarie", "id": "AC000E00000"}
    assert areas[0]["primarySubstation"] == "AC000E00000"
    assert areas[1]["boundary"] is None
    assert areas[1]["primarySubstation"] is None


def test_an_areas_primary_substation_is_its_first_topology_node(downstream) -> None:
    """What the pipelines attribute the area's members to, boundary or not."""
    registry(
        downstream,
        areas={"west": {"name": "West", "topology": ["AC000E00002", "AC000E00001"]}},
    )

    [area] = client.get(AREAS_PATH).json()["areas"]

    assert area == {
        "key": "west",
        "name": "West",
        "boundary": None,
        "primarySubstation": "AC000E00002",
    }


def test_a_rec_with_no_areas_lists_none(downstream) -> None:
    registry(downstream, areas={})

    assert client.get(AREAS_PATH).json()["areas"] == []


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (refusal(404, "community_not_found", "no"), 404, "community_not_found"),
        (httpx.Response(500, text="boom"), 502, "registry_unavailable"),
        (httpx.Response(403, json={"detail": "no"}), 502, "registry_refused"),
    ],
)
def test_the_areas_read_answers_by_code(downstream, answer, status, code) -> None:
    downstream.get(COMMUNITY_URL).mock(return_value=answer)

    response = client.get(AREAS_PATH)

    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}


def test_the_areas_of_another_rec_are_refused(downstream) -> None:
    registry(downstream)
    _as(_manager_of("other_rec"))
    try:
        response = client.get(AREAS_PATH)
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not downstream.routes["community"].called
