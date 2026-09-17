"""The members list: names from the registry, for a manager's screen only.

`respx` stands in front of the **real** `RecRegistryAdminClient`, so the query the
SDK builds and the payload it parses are the ones production sees. A hand-written
fake of the client would agree with whatever this module assumed about it.
"""

import json
import logging

import httpx
import pytest
import respx
from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import Organization
from celine.sdk.rec_registry import RecRegistryAdminClient
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from celine.community.api.deps import get_registry_client, get_user_from_request
from celine.community.api.members import router as members_router
from celine.community.db import get_db
from celine.community.main import app

REGISTRY = "http://registry.test"
REC = "example_rec"
MEMBERS_URL = f"{REGISTRY}/admin/communities/{REC}/members"

client = TestClient(app)


def item(key: str, name: str, **overrides) -> dict:
    return {
        "id": f"id-{key}",
        "key": key,
        "name": name,
        "role": "consumer",
        "status": "active",
        "area": "north",
        "user_id": f"{key.lower()}@example.org",
        "did": f"did:web:example.org:{key.lower()}",
        "delivery_points_count": 2,
        **overrides,
    }


PAGE = {
    "items": [
        item("EX-00001", "Anna Rossi"),
        item("EX-00002", "Participant EX-00002"),
        item("SUB-7F3A", " sub-7f3a "),
        item("EX-00004", "Marco Bianchi", status="suspended", role="prosumer"),
    ],
    "next_cursor": "EX-00004",
}


@pytest.fixture(autouse=True)
def real_registry_client():
    previous = app.dependency_overrides.get(get_registry_client)
    app.dependency_overrides[get_registry_client] = lambda: RecRegistryAdminClient(
        base_url=REGISTRY, default_token="svc-community-token"
    )
    try:
        with respx.mock(assert_all_called=False) as router:
            yield router
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_registry_client, None)
        else:
            app.dependency_overrides[get_registry_client] = previous


def test_only_the_five_fields_a_manager_needs_leave_the_bff(real_registry_client) -> None:
    real_registry_client.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=PAGE))

    response = client.get(f"/api/communities/{REC}/members")

    assert response.status_code == 200
    body = response.json()
    assert body["communityKey"] == REC
    for member in body["items"]:
        assert set(member) == {"key", "name", "role", "status", "area"}
    # Not merely absent as keys: the values appear nowhere in the payload.
    for value in ("@example.org", "did:web", "id-EX-00001"):
        assert value not in response.text, value


def test_the_service_token_and_the_cursor_reach_the_registry_unchanged(
    real_registry_client,
) -> None:
    route = real_registry_client.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=PAGE))

    response = client.get(
        f"/api/communities/{REC}/members",
        params={"cursor": "EX-00000", "limit": 4, "status": "active"},
    )

    assert response.status_code == 200
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer svc-community-token"
    assert dict(sent.url.params) == {"cursor": "EX-00000", "limit": "4", "status": "active"}
    assert response.json()["nextCursor"] == "EX-00004"


def test_the_last_page_has_no_cursor(real_registry_client) -> None:
    real_registry_client.get(MEMBERS_URL).mock(
        return_value=httpx.Response(200, json={"items": PAGE["items"][:1], "next_cursor": None})
    )

    assert client.get(f"/api/communities/{REC}/members").json()["nextCursor"] is None


@pytest.mark.parametrize(
    ("q", "keys"),
    [
        ("rossi", ["EX-00001"]),
        ("ANNA", ["EX-00001"]),
        ("ex-0000", ["EX-00001", "EX-00002", "EX-00004"]),
        ("7f3a", ["SUB-7F3A"]),
        ("nobody", []),
    ],
)
def test_q_matches_name_or_key_within_the_page(real_registry_client, q, keys) -> None:
    route = real_registry_client.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=PAGE))

    body = client.get(f"/api/communities/{REC}/members", params={"q": q}).json()

    assert [member["key"] for member in body["items"]] == keys
    # The registry has no text filter, so none is sent, and the cursor still is.
    assert "q" not in route.calls.last.request.url.params
    assert body["nextCursor"] == "EX-00004"


def test_a_name_that_only_repeats_the_key_is_no_name(real_registry_client) -> None:
    """Onboarding's fallback when a person gave no name is the reference itself."""
    real_registry_client.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=PAGE))

    by_key = {m["key"]: m for m in client.get(f"/api/communities/{REC}/members").json()["items"]}

    assert by_key["SUB-7F3A"]["name"] is None
    # A placeholder is not the key, and nothing here can tell it is not a name.
    assert by_key["EX-00002"]["name"] == "Participant EX-00002"
    assert by_key["EX-00001"]["name"] == "Anna Rossi"


@pytest.mark.parametrize(
    "registry_answer",
    [
        httpx.Response(200, json=PAGE),
        httpx.Response(500, text=json.dumps(PAGE)),
        httpx.ConnectError("refused"),
    ],
)
def test_no_name_reaches_any_log_record(real_registry_client, caplog, registry_answer) -> None:
    """Including a failure whose body is full of names."""
    route = real_registry_client.get(MEMBERS_URL)
    if isinstance(registry_answer, Exception):
        route.mock(side_effect=registry_answer)
    else:
        route.mock(return_value=registry_answer)

    with caplog.at_level(logging.DEBUG):
        client.get(f"/api/communities/{REC}/members", params={"q": "anna"})

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for name in ("Anna", "Rossi", "Bianchi", "@example.org"):
        assert name not in logged, name


def test_the_route_cannot_write_to_the_database() -> None:
    """Names are not persisted: the route has no session to persist them with."""
    route = next(r for r in members_router.routes if isinstance(r, APIRoute))

    def calls(dependant):
        for dependency in dependant.dependencies:
            yield dependency.call
            yield from calls(dependency)

    assert get_db not in set(calls(route.dependant))


def test_an_unknown_community_is_a_404_with_a_code(real_registry_client) -> None:
    real_registry_client.get(MEMBERS_URL).mock(
        return_value=httpx.Response(404, json={"detail": "Community not found"})
    )

    response = client.get(f"/api/communities/{REC}/members")

    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "community_not_found"}


@pytest.mark.parametrize(
    "registry_answer",
    [httpx.Response(500, text="boom"), httpx.Response(422, json={"detail": []})],
)
def test_a_registry_fault_is_a_503_with_a_code(real_registry_client, registry_answer) -> None:
    real_registry_client.get(MEMBERS_URL).mock(return_value=registry_answer)

    response = client.get(f"/api/communities/{REC}/members")

    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "registry_unavailable"}


def test_an_unreachable_registry_is_a_503_with_a_code(real_registry_client) -> None:
    real_registry_client.get(MEMBERS_URL).mock(side_effect=httpx.ConnectTimeout("slow"))

    response = client.get(f"/api/communities/{REC}/members")

    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "registry_unavailable"}


def _as(caller: JwtUser):
    app.dependency_overrides[get_user_from_request] = lambda: caller


def test_a_manager_of_another_rec_is_refused_before_the_registry_is_asked(
    real_registry_client,
) -> None:
    route = real_registry_client.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=PAGE))

    other = {"other_rec": {"type": ["rec"], "groups": ["/managers"]}}
    _as(
        JwtUser(
            sub="manager-of-example",
            organizations=[Organization._from_claim("other_rec", other["other_rec"])],
            claims={"sub": "manager-of-example", "scope": "", "organization": other},
        )
    )
    try:
        response = client.get(f"/api/communities/{REC}/members")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not route.called


def test_a_service_with_community_admin_is_refused(real_registry_client) -> None:
    route = real_registry_client.get(MEMBERS_URL).mock(return_value=httpx.Response(200, json=PAGE))
    _as(
        JwtUser(
            sub="svc",
            preferred_username="service-account-svc-pipelines",
            claims={
                "sub": "svc",
                "preferred_username": "service-account-svc-pipelines",
                "scope": "community.admin community.read",
            },
        )
    )
    try:
        response = client.get(f"/api/communities/{REC}/members")
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not route.called
