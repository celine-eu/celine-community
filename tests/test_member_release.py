"""Release member: the BFF half of a REC admin's press.

`respx` stands in front of onboarding and of the **real**
`OidcClientCredentialsProvider`, so the token request, the headers and the codes
are the ones production sends and reads. The database is a recording session:
what is asserted is the row this module adds, not Postgres.
"""

import logging

import httpx
import pytest
import respx
from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import Organization
from fastapi.testclient import TestClient

from celine.community.api import deps
from celine.community.api.deps import get_user_from_request
from celine.community.db import get_db
from celine.community.db.models import AuditEvent
from celine.community.main import app
from celine.community.settings import settings

ONBOARDING = "http://onboarding.test"
TOKEN_URL = "http://keycloak.test/token"
REC = "example_rec"
MEMBER = "EX-00001"
ADMIN_TOKEN = "tok-admin"
STEPS = ("dataspace_share", "dataspace_identity", "keycloak_user", "rec_registry_member")

RELEASED = {
    "community": REC,
    "memberKey": MEMBER,
    "state": "released",
    "source": "submission",
    "steps": [
        {"step": "dataspace_share", "status": "done", "code": "withdrawn", "detail": "Withdrew 2."},
        {"step": "dataspace_identity", "status": "done", "code": "revoked", "detail": "Revoked."},
        {"step": "keycloak_user", "status": "done", "code": "released", "detail": "Released."},
        {
            "step": "rec_registry_member",
            "status": "done",
            "code": "deactivated",
            "detail": "Set inactive.",
        },
    ],
}

PARTIAL = {
    **RELEASED,
    "state": "partial",
    "source": "registry",
    "steps": [
        {
            "step": "dataspace_share",
            "status": "failed",
            "code": "withdrawal_failed",
            "detail": "Connector at http://connector.internal answered 500.",
        },
        {
            "step": "dataspace_identity",
            "status": "blocked",
            "code": "waits_for_dataspace_share",
            "detail": "Waits.",
        },
        {"step": "keycloak_user", "status": "done", "code": "already_released", "detail": "x"},
        {"step": "rec_registry_member", "status": "done", "code": "a_new_code", "detail": "x"},
    ],
}

client = TestClient(app)


def rec(*groups: str) -> dict:
    return {"type": ["rec"], "groups": list(groups)}


def person(sub: str, orgs: dict | None = None, roles=()) -> JwtUser:
    orgs = orgs or {}
    return JwtUser(
        sub=sub,
        organizations=[Organization._from_claim(alias, data) for alias, data in orgs.items()],
        claims={
            "sub": sub,
            "scope": "",
            "organization": orgs,
            "realm_access": {"roles": list(roles)},
        },
    )


REC_ADMIN = person("rec-admin", {REC: rec("/admins")})
REC_MANAGER = person("rec-manager", {REC: rec("/managers")})
PLATFORM_ADMIN = person("platform-admin", roles=["platform-admin"])


def onboarding_url(community: str = REC, member: str = MEMBER) -> str:
    return f"{ONBOARDING}/api/admin/communities/{community}/members/{member}/release"


def press(*, token: str | None = ADMIN_TOKEN, member: str = MEMBER):
    headers = {"x-auth-request-access-token": token} if token else {}
    return client.post(f"/api/communities/{REC}/members/{member}/release", headers=headers)


class RecordingSession:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.added: list = []
        self.committed = 0
        self.fail_commit = fail_commit

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("database gone")
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


@pytest.fixture
def caller():
    """The REC admin, unless a test sets another caller."""
    current = {"user": REC_ADMIN}
    app.dependency_overrides[get_user_from_request] = lambda: current["user"]
    try:
        yield current
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)


@pytest.fixture(autouse=True)
def downstream(monkeypatch, session, caller):
    monkeypatch.setattr(settings, "onboarding_url", ONBOARDING)
    monkeypatch.setattr(settings, "onboarding_release_scope", "onboarding.members.release")
    provider = deps.onboarding_release_token_provider
    monkeypatch.setattr(provider, "_token", None)
    monkeypatch.setattr(provider._discovery, "_config", None)
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
        router.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "tok-service", "expires_in": 300}
            )
        )
        yield router


def answer(downstream, response):
    route = downstream.post(onboarding_url())
    if isinstance(response, Exception):
        return route.mock(side_effect=response)
    return route.mock(return_value=response)


def refusal(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status, json={"detail": {"code": code, "message": "English message, never shown"}}
    )


# ---------------------------------------------------------------------------
# What goes out
# ---------------------------------------------------------------------------


def test_the_service_token_is_asked_for_the_release_scope_alone(downstream) -> None:
    answer(downstream, httpx.Response(200, json=RELEASED))

    assert press().status_code == 200

    token_request = downstream.routes[1].calls.last.request
    form = dict(httpx.QueryParams(token_request.read().decode()))
    assert form["scope"] == "onboarding.members.release"
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == settings.oidc.client_id


def test_the_email_token_never_carries_the_release_scope() -> None:
    assert deps.onboarding_token_provider is not deps.onboarding_release_token_provider
    assert "release" not in (settings.onboarding_scope or "")


def test_the_admin_token_is_forwarded_as_the_acting_user_with_no_body(downstream) -> None:
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    press()

    sent = route.calls.last.request
    assert sent.method == "POST"
    assert sent.url.path == f"/api/admin/communities/{REC}/members/{MEMBER}/release"
    assert sent.headers["authorization"] == "Bearer tok-service"
    assert sent.headers["x-acting-user-token"] == ADMIN_TOKEN
    # Onboarding refuses a delegated call carrying the proxy's header.
    assert "x-auth-request-access-token" not in sent.headers
    assert sent.read() == b""


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


def test_a_release_answers_the_steps_in_order_without_their_detail(downstream) -> None:
    answer(downstream, httpx.Response(200, json=RELEASED))

    response = press()

    assert response.status_code == 200
    body = response.json()
    assert body["memberKey"] == MEMBER
    assert body["state"] == "released"
    assert body["source"] == "submission"
    assert [step["step"] for step in body["steps"]] == list(STEPS)
    assert body["steps"][0] == {"step": "dataspace_share", "status": "done", "code": "withdrawn"}
    assert "detail" not in response.text


def test_a_partial_release_is_a_200_and_keeps_unknown_codes(downstream, caplog) -> None:
    answer(downstream, httpx.Response(200, json=PARTIAL))

    with caplog.at_level(logging.WARNING):
        response = press()

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "partial"
    assert [(s["status"], s["code"]) for s in body["steps"]] == [
        ("failed", "withdrawal_failed"),
        ("blocked", "waits_for_dataspace_share"),
        ("done", "already_released"),
        ("done", "a_new_code"),
    ]
    # The English detail goes to the log, never to the dashboard.
    assert "connector.internal" not in response.text
    assert any("connector.internal" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("onboarding_answer", "status", "code"),
    [
        (refusal(404, "member_not_found"), 404, "member_not_found"),
        (refusal(404, "community_not_served"), 404, "community_not_served"),
        (refusal(409, "community_ambiguous"), 409, "community_ambiguous"),
        (refusal(502, "registry_unavailable"), 502, "registry_unavailable"),
        (refusal(503, "admin_not_configured"), 503, "admin_not_configured"),
        (refusal(409, "a_code_nobody_has_invented_yet"), 409, "a_code_nobody_has_invented_yet"),
        (refusal(401, "invalid_token"), 502, "onboarding_refused"),
        (refusal(401, "actor_token_invalid"), 502, "onboarding_refused"),
        (refusal(401, "proxy_token_refused"), 502, "onboarding_refused"),
        (refusal(403, "forbidden"), 502, "onboarding_refused"),
        (httpx.Response(500, text="<html>boom</html>"), 502, "http_500"),
        (httpx.Response(200, json={"state": "released"}), 502, "onboarding_unreadable"),
        (httpx.Response(200, text="not json"), 502, "onboarding_unreadable"),
        (httpx.ConnectTimeout("slow"), 503, "onboarding_unavailable"),
        (httpx.ConnectError("refused"), 503, "onboarding_unavailable"),
    ],
)
def test_every_refusal_is_its_code(downstream, session, onboarding_answer, status, code) -> None:
    answer(downstream, onboarding_answer)

    response = press()

    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert "English message" not in response.text
    [row] = session.audit_rows()
    assert row.detail["code"] == code


def test_a_refused_deployment_is_logged_as_an_error(downstream, caplog) -> None:
    answer(downstream, refusal(403, "forbidden"))

    with caplog.at_level(logging.ERROR):
        press()

    assert any(
        record.levelno == logging.ERROR and "Onboarding refused" in record.getMessage()
        for record in caplog.records
    )


def test_a_scope_the_realm_does_not_assign_is_a_refusal_not_an_outage(downstream, caplog) -> None:
    downstream.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_scope",
                "error_description": "Invalid scopes: onboarding.members.release",
            },
        )
    )
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    with caplog.at_level(logging.ERROR):
        response = press()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "onboarding_refused"}}
    assert not route.called
    assert any("onboarding.members.release" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Who may press, and what is refused before onboarding is asked
# ---------------------------------------------------------------------------


def test_a_platform_admin_may_release(downstream, caller) -> None:
    caller["user"] = PLATFORM_ADMIN
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    assert press().status_code == 200
    assert route.called


def test_a_manager_is_refused_before_onboarding_and_with_no_row(downstream, session, caller):
    caller["user"] = REC_MANAGER
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    response = press()

    assert response.status_code == 403
    assert response.json()["detail"] == "REC admins group required"
    assert not route.called
    assert session.added == []


def test_another_recs_admin_is_refused(downstream, session, caller) -> None:
    caller["user"] = person("other-admin", {"other_rec": rec("/admins")})
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    assert press().status_code == 403
    assert not route.called


def test_a_service_holding_community_admin_cannot_release(downstream, caller) -> None:
    caller["user"] = JwtUser(
        sub="svc",
        preferred_username="service-account-svc-pipelines",
        claims={"sub": "svc", "scope": "community.admin"},
    )
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    assert press().status_code == 403
    assert not route.called


@pytest.mark.parametrize(
    ("url", "scope", "code"),
    [
        (None, "onboarding.members.release", "onboarding_not_configured"),
        (ONBOARDING, "", "release_not_configured"),
        (ONBOARDING, None, "release_not_configured"),
    ],
)
def test_without_onboarding_or_the_scope_nothing_is_released(
    downstream, monkeypatch, session, url, scope, code
) -> None:
    monkeypatch.setattr(settings, "onboarding_url", url)
    monkeypatch.setattr(settings, "onboarding_release_scope", scope)
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    response = press()

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": code}}
    assert not route.called
    assert session.added == []


def test_without_an_admin_token_nothing_is_released(downstream, session) -> None:
    route = answer(downstream, httpx.Response(200, json=RELEASED))

    response = press(token=None)

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "acting_token_unavailable"}}
    assert not route.called
    assert session.added == []


# ---------------------------------------------------------------------------
# The audit row
# ---------------------------------------------------------------------------


def test_one_row_per_press_naming_the_key_and_the_step_codes_only(downstream, session) -> None:
    answer(downstream, httpx.Response(200, json=PARTIAL))

    press()

    [row] = session.audit_rows()
    assert session.committed == 1
    assert row.community_key == REC
    assert row.actor_id == REC_ADMIN.sub
    assert row.action == "community.member.release"
    assert row.resource_type == "registry_member"
    assert row.resource_id == MEMBER
    assert row.detail == {
        "code": "partial",
        "status": 200,
        "source": "registry",
        "steps": {
            "dataspace_share": "withdrawal_failed",
            "dataspace_identity": "waits_for_dataspace_share",
            "keycloak_user": "already_released",
            "rec_registry_member": "a_new_code",
        },
    }


def test_a_refusal_row_records_what_onboarding_answered(downstream, session) -> None:
    answer(downstream, refusal(404, "member_not_found"))

    press()

    [row] = session.audit_rows()
    assert row.detail == {"code": "member_not_found", "status": 404, "source": None, "steps": {}}


def test_a_failed_audit_commit_still_answers_the_release(downstream, caplog) -> None:
    failing = RecordingSession(fail_commit=True)
    app.dependency_overrides[get_db] = lambda: failing
    answer(downstream, httpx.Response(200, json=RELEASED))

    with caplog.at_level(logging.ERROR):
        response = press()

    assert response.status_code == 200
    assert any("Audit row NOT written" in r.getMessage() for r in caplog.records)
