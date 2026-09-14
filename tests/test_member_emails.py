"""Send invitation and Reset password: the BFF half of a press.

`respx` stands in front of the **real** `OnboardingAdminClient` and the **real**
`OidcClientCredentialsProvider`, so the token request, the headers and the codes
are the ones production sends and reads. The database is a recording session:
what is asserted is the row this module adds, not Postgres.
"""

import logging

import httpx
import pytest
import respx
from celine.sdk.auth import JwtUser
from fastapi.testclient import TestClient

from celine.community.api import deps
from celine.community.api.deps import get_user_from_request
from celine.community.db import get_db
from celine.community.db.models import AuditEvent
from celine.community.main import app
from celine.community.settings import settings

ONBOARDING = "http://onboarding.test"
TOKEN_URL = "http://keycloak.test/token"
REC = "gr-renewable-community"
MEMBER = "GL-00001"
MANAGER_TOKEN = "tok-manager"
SENT = {"code": "sent", "kind": "invitation", "lifespanSeconds": 604800}

client = TestClient(app)


def onboarding_url(intent: str, community: str = REC, member: str = MEMBER) -> str:
    return f"{ONBOARDING}/api/admin/communities/{community}/members/{member}/{intent}"


def press(intent: str = "invitation", *, token: str | None = MANAGER_TOKEN, member: str = MEMBER):
    headers = {"x-auth-request-access-token": token} if token else {}
    return client.post(f"/api/communities/{REC}/members/{member}/{intent}", headers=headers)


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


@pytest.fixture(autouse=True)
def downstream(monkeypatch, session):
    monkeypatch.setattr(settings, "onboarding_url", ONBOARDING)
    provider = deps.onboarding_token_provider
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


def answer(downstream, intent: str, response):
    route = downstream.post(onboarding_url(intent))
    if isinstance(response, Exception):
        return route.mock(side_effect=response)
    return route.mock(return_value=response)


def refusal(status: int, code: str, **extra) -> httpx.Response:
    return httpx.Response(
        status, json={"detail": {"code": code, "message": "English message, never shown", **extra}}
    )


# ---------------------------------------------------------------------------
# What goes out
# ---------------------------------------------------------------------------


def test_the_service_token_is_asked_for_the_onboarding_scope(downstream) -> None:
    answer(downstream, "invitation", httpx.Response(200, json=SENT))

    assert press().status_code == 200

    token_request = downstream.routes[1].calls.last.request
    form = dict(httpx.QueryParams(token_request.read().decode()))
    assert form["scope"] == "onboarding.members.invite"
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == settings.oidc.client_id


def test_the_manager_token_is_forwarded_as_the_acting_user_and_nowhere_else(downstream) -> None:
    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))

    press()

    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer tok-service"
    assert sent.headers["x-acting-user-token"] == MANAGER_TOKEN
    # Onboarding reads this header first and refuses a delegated call carrying it.
    assert "x-auth-request-access-token" not in sent.headers
    assert sent.read() == b""


@pytest.mark.parametrize(
    ("intent", "kind"), [("invitation", "invitation"), ("password-reset", "password_reset")]
)
def test_each_button_calls_its_own_route_with_the_path_values_unchanged(
    downstream, intent, kind
) -> None:
    routes = {
        name: answer(
            downstream,
            name,
            httpx.Response(200, json={**SENT, "kind": kind if name == intent else "x"}),
        )
        for name in ("invitation", "password-reset")
    }

    response = press(intent)

    assert response.status_code == 200
    assert routes[intent].call_count == 1
    other = "password-reset" if intent == "invitation" else "invitation"
    assert routes[other].call_count == 0
    assert routes[intent].calls.last.request.url.path == (
        f"/api/admin/communities/{REC}/members/{MEMBER}/{intent}"
    )
    assert response.json() == {"code": "sent", "kind": kind, "lifespanSeconds": 604800}


# ---------------------------------------------------------------------------
# What comes back (A6: codes, never messages)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("onboarding_answer", "status", "code"),
    [
        (refusal(404, "member_not_found"), 404, "member_not_found"),
        (refusal(404, "account_not_found"), 404, "account_not_found"),
        (refusal(404, "community_not_found"), 404, "community_not_found"),
        (refusal(404, "community_not_served"), 404, "community_not_served"),
        (refusal(409, "account_disabled"), 409, "account_disabled"),
        (refusal(409, "has_password"), 409, "has_password"),
        (refusal(409, "no_password"), 409, "no_password"),
        (refusal(409, "no_email"), 409, "no_email"),
        (refusal(409, "community_ambiguous"), 409, "community_ambiguous"),
        (refusal(502, "send_failed"), 502, "send_failed"),
        (refusal(502, "registry_unavailable"), 502, "registry_unavailable"),
        (refusal(502, "provisioning_failed"), 502, "provisioning_failed"),
        (refusal(502, "provisioning_refused"), 502, "provisioning_refused"),
        (refusal(503, "provisioning_not_configured"), 503, "provisioning_not_configured"),
        (refusal(503, "provisioning_unavailable"), 503, "provisioning_unavailable"),
        (refusal(409, "a_code_nobody_has_invented_yet"), 409, "a_code_nobody_has_invented_yet"),
        (refusal(401, "actor_token_invalid"), 502, "onboarding_refused"),
        (refusal(401, "proxy_token_refused"), 502, "onboarding_refused"),
        (refusal(403, "forbidden"), 502, "onboarding_refused"),
        (httpx.Response(500, text="<html>boom</html>"), 502, "http_500"),
        (httpx.ConnectTimeout("slow"), 503, "onboarding_unavailable"),
        (httpx.ConnectError("refused"), 503, "onboarding_unavailable"),
    ],
)
def test_every_outcome_is_its_code(downstream, session, onboarding_answer, status, code) -> None:
    answer(downstream, "invitation", onboarding_answer)

    response = press()

    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert "English message" not in response.text
    # Refusals are audited like sends.
    [row] = session.audit_rows()
    assert row.detail["code"] == code


def test_not_on_dev_list_is_a_200_that_sent_nothing(downstream) -> None:
    answer(downstream, "invitation", httpx.Response(200, json={**SENT, "code": "not_on_dev_list"}))

    response = press()

    assert response.status_code == 200
    assert response.json()["code"] == "not_on_dev_list"


def test_a_cooldown_says_when_to_try_again(downstream) -> None:
    answer(
        downstream,
        "password-reset",
        httpx.Response(
            429,
            json={"detail": {"code": "cooldown", "message": "wait", "retryAfterSeconds": 240}},
            headers={"Retry-After": "240"},
        ),
    )

    response = press("password-reset")

    assert response.status_code == 429
    assert response.json() == {"detail": {"code": "cooldown", "retryAfterSeconds": 240}}
    assert response.headers["retry-after"] == "240"


def test_a_refused_deployment_is_logged_as_an_error(downstream, caplog) -> None:
    answer(downstream, "invitation", refusal(403, "forbidden"))

    with caplog.at_level(logging.ERROR):
        press()

    assert any(
        record.levelno == logging.ERROR and "Onboarding refused" in record.getMessage()
        for record in caplog.records
    )


def test_a_scope_the_realm_does_not_know_is_a_refusal_not_an_outage(downstream, caplog) -> None:
    """Measured on the dev realm before the scope was synced: `400 invalid_scope`."""
    downstream.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_scope",
                "error_description": "Invalid scopes: onboarding.members.invite",
            },
        )
    )
    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))

    with caplog.at_level(logging.ERROR):
        response = press()

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "onboarding_refused"}}
    assert not route.called
    assert any("onboarding.members.invite" in r.getMessage() for r in caplog.records)


def test_an_unreachable_token_endpoint_is_onboarding_unavailable(downstream) -> None:
    downstream.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("keycloak down"))
    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))

    response = press()

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "onboarding_unavailable"}}
    assert not route.called


# ---------------------------------------------------------------------------
# What is refused before onboarding is asked
# ---------------------------------------------------------------------------


def test_a_policy_denial_makes_no_onboarding_call_and_no_row(downstream, session) -> None:
    from celine.sdk.auth.jwt import Organization

    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))
    other = {"example_rec": {"type": ["rec"], "groups": ["/managers"]}}
    app.dependency_overrides[get_user_from_request] = lambda: JwtUser(
        sub="manager-of-example",
        organizations=[Organization._from_claim("example_rec", other["example_rec"])],
        claims={"sub": "manager-of-example", "scope": "", "organization": other},
    )
    try:
        response = press()
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not route.called
    assert session.added == []


def test_a_service_holding_community_admin_cannot_send(downstream, session) -> None:
    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))
    app.dependency_overrides[get_user_from_request] = lambda: JwtUser(
        sub="svc",
        preferred_username="service-account-svc-pipelines",
        claims={"sub": "svc", "scope": "community.admin"},
    )
    try:
        response = press()
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not route.called


def test_without_onboarding_url_nothing_is_sent(downstream, monkeypatch, session) -> None:
    monkeypatch.setattr(settings, "onboarding_url", None)
    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))

    response = press()

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "onboarding_not_configured"}}
    assert not route.called
    assert session.added == []


def test_without_a_manager_token_nothing_is_sent(downstream, session) -> None:
    """Only development authentication can get here, and it must not loop on a 401."""
    route = answer(downstream, "invitation", httpx.Response(200, json=SENT))

    response = press(token=None)

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "acting_token_unavailable"}}
    assert not route.called
    assert session.added == []


# ---------------------------------------------------------------------------
# The audit row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "action"),
    [
        ("invitation", "community.member.invitation"),
        ("password-reset", "community.member.password_reset"),
    ],
)
def test_one_row_per_press_naming_the_key_only(downstream, session, intent, action) -> None:
    kind = intent.replace("-", "_")
    answer(downstream, intent, httpx.Response(200, json={**SENT, "kind": kind}))

    press(intent)

    [row] = session.audit_rows()
    assert session.committed == 1
    assert row.community_key == REC
    assert row.actor_id == settings.dev_user_sub
    assert row.action == action
    assert row.resource_type == "registry_member"
    assert row.resource_id == MEMBER
    assert row.detail == {"code": "sent", "kind": kind, "lifespan_seconds": 604800, "status": 200}


def test_a_refusal_row_records_what_onboarding_answered(downstream, session) -> None:
    answer(downstream, "password-reset", refusal(409, "no_password"))

    press("password-reset")

    [row] = session.audit_rows()
    assert row.detail == {
        "code": "no_password",
        "kind": "password_reset",
        "lifespan_seconds": None,
        "status": 409,
    }


def test_an_unreachable_onboarding_is_audited_with_no_status(downstream, session) -> None:
    answer(downstream, "invitation", httpx.ConnectError("refused"))

    press()

    [row] = session.audit_rows()
    assert row.detail["status"] is None


def test_a_failed_audit_commit_after_a_send_still_answers_sent(downstream, caplog) -> None:
    """The email has gone. Telling the manager otherwise invites a second one."""
    failing = RecordingSession(fail_commit=True)
    app.dependency_overrides[get_db] = lambda: failing
    answer(downstream, "invitation", httpx.Response(200, json=SENT))

    with caplog.at_level(logging.ERROR):
        response = press()

    assert response.status_code == 200
    assert response.json()["code"] == "sent"
    [record] = [r for r in caplog.records if "Audit row NOT written" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert MEMBER in record.getMessage()
    assert settings.dev_user_sub in record.getMessage()
