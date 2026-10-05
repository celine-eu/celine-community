"""Refusals leave a record naming the caller; the API documentation is a dev convenience.

The refusals are read back off the `celine.audit` logger the way a log pipeline would
select them: one JSON object per line. The policy is the real one; only the caller is
handed in.
"""

import json
import logging
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import Organization
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from celine.community.api import deps
from celine.community.api.deps import get_user_from_request
from celine.community.main import app, create_app
from celine.community.settings import settings

EMAIL = "viewer@example.org"
NAME = "Example Viewer"


def viewer() -> JwtUser:
    """A member of `example_rec` holding only a viewer group: no capability."""
    org = {"type": ["rec"], "groups": ["/viewers"]}
    claims = {
        "sub": "ex-00001",
        "azp": "community-dashboard",
        "email": EMAIL,
        "name": NAME,
        "scope": "community.read",
        "organization": {"example_rec": org},
    }
    return JwtUser(
        sub="ex-00001",
        email=EMAIL,
        name=NAME,
        organizations=[Organization._from_claim("example_rec", org)],
        claims=claims,
    )


def denials(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "celine.audit" and r.levelno == logging.WARNING
    ]


def assert_no_personal_data(caplog) -> None:
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "celine.audit")
    assert EMAIL not in text
    assert NAME not in text


@pytest.fixture
def as_viewer():
    app.dependency_overrides[get_user_from_request] = viewer
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)


def test_a_refused_rec_read_is_recorded_with_the_caller_and_the_rec(as_viewer, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = as_viewer.get("/api/communities/example_rec/overview")

    assert response.status_code == 403
    [record] = denials(caplog)
    assert record["event"] == "denied"
    assert record["outcome"] == "denied"
    assert record["service"] == "community-api"
    assert record["action"] == "community.read"
    assert record["sub"] == "ex-00001"
    assert record["client_id"] == "community-dashboard"
    assert record["resource"] == "example_rec"
    assert record["method"] == "GET"
    assert record["route"] == "/api/communities/{community_key}/overview"
    assert record["reason"]
    assert_no_personal_data(caplog)


def test_a_caller_who_manages_nothing_is_recorded_once(as_viewer, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="celine.audit"):
        response = as_viewer.get("/api/me")

    assert response.status_code == 403
    [record] = denials(caplog)
    assert record["action"] == "console.read"
    assert record["sub"] == "ex-00001"
    assert record["reason"] == "no_rec"
    assert_no_personal_data(caplog)


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (pyjwt.ExpiredSignatureError("expired"), "token_expired"),
        (pyjwt.InvalidTokenError("bad"), "token_invalid"),
        (RuntimeError("jwks unreachable"), "token_unverified"),
    ],
)
def test_a_presented_token_that_fails_is_recorded_without_a_caller(
    monkeypatch, caplog, raised, reason
) -> None:
    def from_token(token, **_):
        raise raised

    monkeypatch.setattr(settings, "dev_auth_enabled", False)
    monkeypatch.setattr(deps, "JwtUser", SimpleNamespace(from_token=from_token))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/me",
            "headers": [(b"authorization", b"Bearer not-a-jwt")],
        }
    )

    with caplog.at_level(logging.INFO, logger="celine.audit"), pytest.raises(HTTPException) as exc:
        get_user_from_request(request)

    assert exc.value.status_code == 401
    [record] = denials(caplog)
    assert record["action"] == "authenticate"
    assert record["sub"] is None
    assert record["reason"] == reason


def test_no_token_at_all_records_nothing(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "dev_auth_enabled", False)
    request = Request({"type": "http", "method": "GET", "path": "/api/me", "headers": []})

    with caplog.at_level(logging.INFO, logger="celine.audit"), pytest.raises(HTTPException):
        get_user_from_request(request)

    assert denials(caplog) == []


# ---------------------------------------------------------------------------
# API documentation
# ---------------------------------------------------------------------------

DOC_PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")


def _statuses(monkeypatch, env: str) -> dict[str, int]:
    from celine.community import main as main_module

    monkeypatch.setattr(settings, "celine_env", env)
    monkeypatch.setattr(settings, "environment", "")
    # Outside dev the startup guard refuses the suite's dev defaults; not under test.
    monkeypatch.setattr(
        main_module, "posture_guard", lambda s: SimpleNamespace(enforce=lambda: None)
    )
    client = TestClient(create_app())
    return {path: client.get(path).status_code for path in (*DOC_PATHS, "/health")}


def test_outside_dev_the_documentation_is_not_mounted(monkeypatch) -> None:
    monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)
    monkeypatch.setattr(settings, "dev_auth_enabled", False)

    statuses = _statuses(monkeypatch, "staging")

    assert {p: statuses[p] for p in DOC_PATHS} == {p: 404 for p in DOC_PATHS}
    assert statuses["/health"] == 200


def test_outside_dev_the_documentation_can_be_published_on_purpose(monkeypatch) -> None:
    monkeypatch.setenv("CELINE_PUBLIC_DOCS", "true")
    monkeypatch.setattr(settings, "dev_auth_enabled", False)

    statuses = _statuses(monkeypatch, "staging")

    assert all(statuses[p] == 200 for p in DOC_PATHS)


def test_in_dev_the_documentation_is_served(monkeypatch) -> None:
    monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)

    statuses = _statuses(monkeypatch, "dev")

    assert all(statuses[p] == 200 for p in DOC_PATHS)


def test_the_exported_contract_does_not_depend_on_the_docs_route(monkeypatch) -> None:
    """`task openapi` reads `app.openapi()`, which outside dev still answers."""
    from celine.community import main as main_module

    monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)
    monkeypatch.setattr(settings, "celine_env", "staging")
    monkeypatch.setattr(settings, "environment", "")
    monkeypatch.setattr(settings, "dev_auth_enabled", False)
    monkeypatch.setattr(
        main_module, "posture_guard", lambda s: SimpleNamespace(enforce=lambda: None)
    )

    assert "/api/me" in create_app().openapi()["paths"]
