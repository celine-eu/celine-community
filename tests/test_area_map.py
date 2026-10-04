"""The read-only area map: each area's boundary shape (ADR-0003).

`respx` stands in front of the **real** `RecRegistryAdminClient`, the **real**
`DTClient` and the **real** `OidcClientCredentialsProvider`s, so the token
requests, the paths and the bodies are the ones production sends. The shapes are
synthetic squares off land (latitude 0–0.1, longitude 0–0.4), and the boundary
ids are placeholders.
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
from celine.community.api.area_map import MAX_IDS_PER_CALL
from celine.community.api.deps import get_user_from_request
from celine.community.main import app
from celine.community.services.cache import aggregate_cache
from celine.community.settings import settings

REGISTRY = "http://registry.test"
DT = "http://dt.test"
TOKEN_URL = "http://keycloak.test/token"
REC = "example_rec"
COMMUNITY_URL = f"{REGISTRY}/admin/communities/{REC}"
SHAPE_URL = f"{DT}/communities/it/{REC}/values/boundary_shape"
SHAPES_PATH = f"/api/communities/{REC}/areas/shapes"

READ_TOKEN = "tok-read"
DT_TOKEN = "tok-dt"
DT_SCOPE = "digital-twin.values.read dataset.query"
SOURCE = "gse_cabine_primarie"

client = TestClient(app)


def square(lon: float, lat: float, side: float = 0.05) -> dict:
    """A synthetic polygon in the Gulf of Guinea, nowhere near land."""
    ring = [
        [lon, lat],
        [lon + side, lat],
        [lon + side, lat + side],
        [lon, lat + side],
        [lon, lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


SHAPES = {
    "AC000E00001": square(0.10, 0.02),
    "AC000E00002": square(0.25, 0.04),
}


def _token_for(request: httpx.Request) -> httpx.Response:
    form = dict(httpx.QueryParams(request.read().decode()))
    scope = form.get("scope", "")
    if scope == "rec-registry.read":
        return httpx.Response(200, json={"access_token": READ_TOKEN, "expires_in": 300})
    if scope == DT_SCOPE:
        return httpx.Response(200, json={"access_token": DT_TOKEN, "expires_in": 300})
    return httpx.Response(400, json={"error": "invalid_scope"})


@pytest.fixture(autouse=True)
def downstream(monkeypatch):
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    monkeypatch.setattr(settings, "rec_registry_url", REGISTRY)
    monkeypatch.setattr(settings, "digital_twin_api_url", DT)
    for provider in (deps.registry_token_provider, deps.dt_token_provider):
        monkeypatch.setattr(provider, "_token", None)
        monkeypatch.setattr(provider._discovery, "_config", None)
    monkeypatch.setattr(deps.dt_token_provider, "_scope", DT_SCOPE)
    aggregate_cache.clear()
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
    aggregate_cache.clear()


def community(areas: dict) -> dict:
    return {"id": "id-rec", "key": REC, "name": "Example REC", "areas": areas, "topology": []}


def area(name: str, boundary_id: str | None, source: str = SOURCE) -> dict:
    value: dict = {"name": name}
    if boundary_id is not None:
        value["boundary"] = {"source": source, "id": boundary_id}
        value["topology"] = [boundary_id]
    return value


def registry(downstream, areas: dict):
    return downstream.get(COMMUNITY_URL, name="community").mock(
        return_value=httpx.Response(200, json=community(areas))
    )


def dt_answering(downstream, shapes: dict | None = None):
    """The DT's `boundary_shape`: one row per requested id it knows, `geojson` a string."""
    known = SHAPES if shapes is None else shapes

    def answer(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)["payload"]
        rows = [
            {"id": boundary_id, "geojson": json.dumps(known[boundary_id])}
            for boundary_id in sorted(payload["ids"])
            if boundary_id in known
        ]
        return httpx.Response(
            200, json={"items": rows, "count": len(rows), "limit": 100, "offset": 0}
        )

    return downstream.post(SHAPE_URL, name="shape").mock(side_effect=answer)


def payloads(route) -> list[dict]:
    return [json.loads(call.request.content)["payload"] for call in route.calls]


def _as(caller: JwtUser) -> None:
    app.dependency_overrides[get_user_from_request] = lambda: caller


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def test_each_area_with_a_boundary_is_answered_with_its_shape(downstream) -> None:
    registry(
        downstream,
        {
            "south": area("South", "AC000E00002"),
            "north": area("North", "AC000E00001"),
            "unmapped": area("Unmapped", None),
        },
    )
    shape = dt_answering(downstream)

    response = client.get(SHAPES_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "communityKey": REC,
        "areas": [
            {
                "areaKey": "north",
                "name": "North",
                "boundaryId": "AC000E00001",
                "geometry": SHAPES["AC000E00001"],
            },
            {
                "areaKey": "south",
                "name": "South",
                "boundaryId": "AC000E00002",
                "geometry": SHAPES["AC000E00002"],
            },
        ],
    }
    assert payloads(shape) == [{"source": SOURCE, "ids": ["AC000E00001", "AC000E00002"]}]


def test_the_dt_is_called_with_the_default_dt_token_and_the_registry_with_the_read_token(
    downstream,
) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    shape = dt_answering(downstream)

    assert client.get(SHAPES_PATH).status_code == 200

    assert shape.calls.last.request.headers["authorization"] == f"Bearer {DT_TOKEN}"
    assert downstream.routes["community"].calls.last.request.headers["authorization"] == (
        f"Bearer {READ_TOKEN}"
    )


def test_a_boundary_the_dt_does_not_know_has_no_geometry(downstream) -> None:
    registry(
        downstream, {"north": area("North", "AC000E00001"), "gone": area("Gone", "AC000E09999")}
    )
    dt_answering(downstream)

    areas = {a["areaKey"]: a for a in client.get(SHAPES_PATH).json()["areas"]}

    assert areas["north"]["geometry"] == SHAPES["AC000E00001"]
    assert areas["gone"]["geometry"] is None
    assert areas["gone"]["boundaryId"] == "AC000E09999"


def test_an_unparsable_shape_has_no_geometry(downstream) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    downstream.post(SHAPE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"id": "AC000E00001", "geojson": "not json"}],
                "count": 1,
                "limit": 100,
                "offset": 0,
            },
        )
    )

    [only] = client.get(SHAPES_PATH).json()["areas"]

    assert only["geometry"] is None


def test_two_areas_on_one_boundary_ask_for_it_once(downstream) -> None:
    """Imported areas can share a substation; the map shows both over one shape."""
    registry(
        downstream,
        {"a": area("A", "AC000E00001"), "b": area("B", "AC000E00001")},
    )
    shape = dt_answering(downstream)

    areas = client.get(SHAPES_PATH).json()["areas"]

    assert [a["geometry"] for a in areas] == [SHAPES["AC000E00001"]] * 2
    assert payloads(shape) == [{"source": SOURCE, "ids": ["AC000E00001"]}]


def test_more_boundaries_than_one_call_takes_are_asked_in_chunks(downstream) -> None:
    count = MAX_IDS_PER_CALL + 3
    ids = [f"AC000E{n:05d}" for n in range(count)]
    registry(downstream, {f"area-{n:03d}": area(f"Area {n}", ids[n]) for n in range(count)})
    shape = dt_answering(downstream, {i: square(0.1, 0.02, 0.001) for i in ids})

    areas = client.get(SHAPES_PATH).json()["areas"]

    assert len(areas) == count
    assert all(a["geometry"] is not None for a in areas)
    assert [len(p["ids"]) for p in payloads(shape)] == [MAX_IDS_PER_CALL, 3]


def test_a_rec_without_boundaries_answers_no_areas_and_asks_the_dt_nothing(downstream) -> None:
    registry(downstream, {"north": area("North", None)})
    shape = dt_answering(downstream)

    response = client.get(SHAPES_PATH)

    assert response.json() == {"communityKey": REC, "areas": []}
    assert not shape.called


def test_the_answer_is_cached_briefly(downstream) -> None:
    community_route = registry(downstream, {"north": area("North", "AC000E00001")})
    shape = dt_answering(downstream)

    first = client.get(SHAPES_PATH).json()
    second = client.get(SHAPES_PATH).json()

    assert first == second
    assert shape.call_count == 1
    assert community_route.call_count == 1


# ---------------------------------------------------------------------------
# Failures answer by code, never a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(500, text="boom"), "digital_twin_unavailable"),
        (httpx.Response(404, json={"detail": "no such fetcher"}), "digital_twin_unavailable"),
        (httpx.ConnectError("down"), "digital_twin_unavailable"),
        (httpx.Response(401, json={"detail": "no"}), "digital_twin_refused"),
        (httpx.Response(403, json={"detail": "no"}), "digital_twin_refused"),
    ],
    ids=["500", "404", "unreachable", "401", "403"],
)
def test_a_dt_failure_answers_by_code(downstream, caplog, answer, code) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    if isinstance(answer, Exception):
        downstream.post(SHAPE_URL).mock(side_effect=answer)
    else:
        downstream.post(SHAPE_URL).mock(return_value=answer)

    with caplog.at_level(logging.DEBUG):
        response = client.get(SHAPES_PATH)

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": code}}


def test_keycloak_refusing_the_dt_token_is_dt_refused(downstream, monkeypatch) -> None:
    monkeypatch.setattr(deps.dt_token_provider, "_scope", "digital-twin.unknown")
    registry(downstream, {"north": area("North", "AC000E00001")})
    shape = dt_answering(downstream)

    response = client.get(SHAPES_PATH)

    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "digital_twin_refused"}}
    assert not shape.called


def test_a_dt_failure_is_not_cached(downstream) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    downstream.post(SHAPE_URL, name="shape").mock(return_value=httpx.Response(500, text="boom"))
    assert client.get(SHAPES_PATH).status_code == 502

    dt_answering(downstream)

    assert client.get(SHAPES_PATH).status_code == 200


def test_without_a_dt_configured_the_map_answers_by_code(downstream, monkeypatch) -> None:
    monkeypatch.setattr(settings, "digital_twin_api_url", None)
    registry(downstream, {"north": area("North", "AC000E00001")})

    response = client.get(SHAPES_PATH)

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "digital_twin_not_configured"}}


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (
            httpx.Response(404, json={"detail": "no", "code": "community_not_found"}),
            404,
            "community_not_found",
        ),
        (httpx.Response(500, text="boom"), 502, "registry_unavailable"),
        (httpx.Response(403, json={"detail": "no"}), 502, "registry_refused"),
    ],
)
def test_a_registry_failure_answers_by_code(downstream, answer, status, code) -> None:
    downstream.get(COMMUNITY_URL).mock(return_value=answer)
    shape = dt_answering(downstream)

    response = client.get(SHAPES_PATH)

    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert not shape.called


def test_no_shape_or_coordinate_reaches_the_log(downstream, caplog) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    dt_answering(downstream)

    with caplog.at_level(logging.DEBUG):
        client.get(SHAPES_PATH)
        aggregate_cache.clear()
        downstream.post(SHAPE_URL).mock(return_value=httpx.Response(500, text="boom"))
        client.get(SHAPES_PATH)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "coordinates" not in logged
    assert "0.02" not in logged and "0.15" not in logged


# ---------------------------------------------------------------------------
# Who may read it: `community.read`, as `GET …/areas`
# ---------------------------------------------------------------------------


def test_the_shapes_of_another_rec_are_refused(downstream) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    shape = dt_answering(downstream)
    orgs = {"other_rec": {"type": ["rec"], "groups": ["/managers"]}}
    _as(
        JwtUser(
            sub="manager-of-other",
            organizations=[Organization._from_claim("other_rec", orgs["other_rec"])],
            claims={"sub": "manager-of-other", "scope": "", "organization": orgs},
        )
    )
    try:
        response = client.get(SHAPES_PATH)
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
    assert not downstream.routes["community"].called
    assert not shape.called


def test_a_platform_admin_reads_the_shapes_of_any_rec(downstream) -> None:
    registry(downstream, {"north": area("North", "AC000E00001")})
    dt_answering(downstream)
    _as(
        JwtUser(
            sub="platform-admin",
            claims={"sub": "platform-admin", "realm_access": {"roles": ["platform-admin"]}},
        )
    )
    try:
        response = client.get(SHAPES_PATH)
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "odd",
    [
        pytest.param({"source": "some_other_source", "id": "AC000E00002"}, id="source-off-enum"),
        pytest.param({"source": SOURCE, "id": "AC000E" + "0" * 59}, id="id-over-64"),
    ],
)
def test_a_boundary_the_dt_would_refuse_has_no_geometry_and_the_rest_still_answer(
    downstream, odd: dict
) -> None:
    """The registry stores any `{source, id}`; the fetcher refuses a whole request
    for one id over 64 characters or a source outside its enum. That area is not
    sent and has no geometry; the other areas still get their shapes."""
    odd_area = area("Odd", odd["id"], source=odd["source"])
    registry(downstream, {"north": area("North", "AC000E00001"), "odd": odd_area})
    shape = dt_answering(downstream)

    response = client.get(SHAPES_PATH)

    assert response.status_code == 200
    areas = {a["areaKey"]: a for a in response.json()["areas"]}
    assert areas["north"]["geometry"] == SHAPES["AC000E00001"]
    assert areas["odd"]["geometry"] is None
    assert areas["odd"]["boundaryId"] == odd["id"]
    assert payloads(shape) == [{"source": SOURCE, "ids": ["AC000E00001"]}]


def test_a_boundary_id_of_exactly_64_characters_is_asked_for(downstream) -> None:
    long_id = "AC000E" + "0" * 58
    registry(downstream, {"north": area("North", long_id)})
    shape = dt_answering(downstream, {long_id: square(0.1, 0.02)})

    [only] = client.get(SHAPES_PATH).json()["areas"]

    assert only["geometry"] == square(0.1, 0.02)
    assert payloads(shape) == [{"source": SOURCE, "ids": [long_id]}]
