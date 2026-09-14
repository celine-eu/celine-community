"""The sends view: audit rows filtered in SQL, names read back from the registry.

Real Postgres, as `test_api.py` uses, because the filters are SQL. The rows are
inserted with a member-key prefix no real member has, and removed afterwards.
`respx` stands in front of the real registry client for the names.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import psycopg2
import pytest
import respx
from celine.sdk.auth import JwtUser
from celine.sdk.rec_registry import RecRegistryAdminClient
from fastapi.testclient import TestClient
from psycopg2.extras import Json
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from celine.community.api.deps import get_registry_client, get_user_from_request
from celine.community.db import get_db
from celine.community.main import app
from celine.community.settings import settings

REGISTRY = "http://registry.test"
REC = "gr-renewable-community"
PREFIX = "TEST-SENDS-"
SENDS = f"/api/communities/{REC}/members/sends"
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

client = TestClient(app)

#: (member, action, actor, code, minutes after T0, community)
ROWS = [
    ("A", "community.member.invitation", "manager-1", "sent", 0, REC),
    ("A", "community.member.password_reset", "manager-1", "no_password", 1, REC),
    ("A", "community.member.invitation", "manager-2", "cooldown", 2, REC),
    ("B", "community.member.invitation", "manager-2", "no_email", 3, REC),
    ("B", "community.member.password_reset", "manager-1", "sent", 4, REC),
    # Not a send, same member: must never appear.
    ("A", "community.alert.acknowledge", "manager-1", "sent", 5, REC),
    # A send in another REC: must never appear.
    ("A", "community.member.invitation", "manager-1", "sent", 6, "example_rec"),
]


def _database_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


@contextmanager
def send_rows():
    with psycopg2.connect(_database_url()) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM audit_events WHERE resource_id LIKE %s", (f"{PREFIX}%",))
        for member, action, actor, code, minutes, community in ROWS:
            cursor.execute(
                """
                INSERT INTO audit_events
                    (id, community_key, actor_id, action, resource_type, resource_id, detail,
                     created_at)
                VALUES (gen_random_uuid(), %s, %s, %s, 'registry_member', %s, %s, %s)
                """,
                (
                    community,
                    actor,
                    action,
                    f"{PREFIX}{member}",
                    Json({"code": code, "kind": None, "lifespan_seconds": None, "status": 200}),
                    T0 + timedelta(minutes=minutes),
                ),
            )
    try:
        yield
    finally:
        with psycopg2.connect(_database_url()) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_events WHERE resource_id LIKE %s", (f"{PREFIX}%",))


@pytest.fixture(scope="module", autouse=True)
def rows_and_client():
    # Its own unpooled engine: the application's pool may still hold connections
    # opened on another test module's event loop.
    sessions = async_sessionmaker(
        create_async_engine(settings.database_url, poolclass=NullPool),
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def unpooled_db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = unpooled_db
    try:
        with send_rows(), client:
            yield
    finally:
        app.dependency_overrides.pop(get_db, None)


def member(key: str, name: str) -> dict:
    return {
        "id": f"id-{key}",
        "key": key,
        "name": name,
        "role": "consumer",
        "status": "active",
        "area": "north",
        "user_id": f"{key.lower()}@example.org",
        "did": None,
        "delivery_points_count": 1,
    }


@pytest.fixture(autouse=True)
def registry():
    app.dependency_overrides[get_registry_client] = lambda: RecRegistryAdminClient(
        base_url=REGISTRY, default_token="svc"
    )
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{REGISTRY}/admin/communities/{REC}/members").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "items": [
                            member(f"{PREFIX}A", "Anna Rossi"),
                            member(f"{PREFIX}B", f"{PREFIX}b"),
                        ],
                        "next_cursor": None,
                    },
                )
            )
            yield router
    finally:
        app.dependency_overrides.pop(get_registry_client, None)


def sends(**params) -> dict:
    response = client.get(SENDS, params={"member_key": None, **params})
    assert response.status_code == 200, response.text
    return response.json()


def ours(body: dict) -> list[dict]:
    return [item for item in body["items"] if item["memberKey"].startswith(PREFIX)]


def test_only_this_recs_sends_newest_first_with_names_read_back() -> None:
    body = sends(**{"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat()})

    items = ours(body)
    assert [(i["memberKey"][-1], i["intent"], i["code"]) for i in items] == [
        ("B", "password_reset", "sent"),
        ("B", "invitation", "no_email"),
        ("A", "invitation", "cooldown"),
        ("A", "password_reset", "no_password"),
        ("A", "invitation", "sent"),
    ]
    assert body["namesAvailable"] is True
    assert {i["memberKey"]: i["memberName"] for i in items} == {
        f"{PREFIX}A": "Anna Rossi",
        f"{PREFIX}B": None,  # the registry's name only repeats the key
    }
    assert set(items[0]) == {
        "id",
        "createdAt",
        "memberKey",
        "memberName",
        "intent",
        "code",
        "actorId",
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"member_key": f"{PREFIX}A"}, ["cooldown", "no_password", "sent"]),
        ({"actor": "manager-2"}, ["no_email", "cooldown"]),
        ({"intent": "password_reset"}, ["sent", "no_password"]),
        ({"code": "sent"}, ["sent", "sent"]),
        ({"from": (T0 + timedelta(minutes=2)).isoformat()}, ["sent", "no_email", "cooldown"]),
        ({"to": (T0 + timedelta(minutes=2)).isoformat()}, ["no_password", "sent"]),
        ({"member_key": f"{PREFIX}A", "intent": "invitation", "actor": "manager-1"}, ["sent"]),
    ],
)
def test_each_filter_narrows_the_rows(params, expected) -> None:
    window = {"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat()}
    body = sends(**{**window, **params})

    assert [item["code"] for item in ours(body)] == expected


def test_the_cursor_pages_through_without_repeats() -> None:
    window = {"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat(), "limit": 2}
    seen, cursor = [], None
    for _ in range(5):
        body = sends(**window, **({"cursor": cursor} if cursor else {}))
        seen.extend(item["id"] for item in ours(body))
        cursor = body["nextCursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5


def test_code_filters_inside_the_page_and_keeps_the_sql_cursor() -> None:
    window = {"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat(), "limit": 2}

    body = sends(**window, code="no_password")

    # The two newest rows are B's; neither is `no_password`, and there are more pages.
    assert ours(body) == []
    assert body["nextCursor"]


def test_a_registry_outage_keeps_the_rows_and_drops_the_names(registry) -> None:
    registry.get(f"{REGISTRY}/admin/communities/{REC}/members").mock(
        side_effect=httpx.ConnectError("down")
    )

    body = sends(**{"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat()})

    assert body["namesAvailable"] is False
    assert len(ours(body)) == 5
    assert all(item["memberName"] is None for item in ours(body))


def test_a_malformed_cursor_is_a_422_with_a_code() -> None:
    response = client.get(SENDS, params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "invalid_cursor"}


def test_the_names_are_not_written_anywhere() -> None:
    sends(**{"from": T0.isoformat(), "to": (T0 + timedelta(hours=1)).isoformat()})

    with psycopg2.connect(_database_url()) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM audit_events WHERE detail::text LIKE %s OR detail::text LIKE %s",
            ("%Anna%", "%Rossi%"),
        )
        assert cursor.fetchone()[0] == 0


def test_the_policy_applies() -> None:
    from celine.sdk.auth.jwt import Organization

    other = {"example_rec": {"type": ["rec"], "groups": ["/managers"]}}
    app.dependency_overrides[get_user_from_request] = lambda: JwtUser(
        sub="manager-of-example",
        organizations=[Organization._from_claim("example_rec", other["example_rec"])],
        claims={"sub": "manager-of-example", "scope": "", "organization": other},
    )
    try:
        response = client.get(SENDS)
    finally:
        app.dependency_overrides.pop(get_user_from_request, None)

    assert response.status_code == 403
