"""The two-level model against real tokens from a local Keycloak.

Opt-in: set `CELINE_COMMUNITY_KEYCLOAK_URL` (e.g. `http://keycloak.celine.localhost`)
to a **local** Keycloak whose realm `celine` has been converged by celine-policies
(`keycloak bootstrap` + `seed-dev-users`): the `platform-admin` realm role held by
`admin`, `org-admin` in `example_rec`'s `admins`, `org-viewer` in its `viewers`, and no
realm groups. Everything else is skipped, never faked.

Tokens are minted on `oauth2_proxy` (the only client that issues user tokens), with
the scope oauth2-proxy asks for, and verified exactly as the service verifies them:
signature against the realm's JWKS, issuer and the `svc-community` audience.

A token that still carries the retired realm group `/admins` cannot be minted from a
converged realm. Pass one in `CELINE_COMMUNITY_LEGACY_TOKEN` (an access token for a
member of realm `/admins` who is also in `example_rec`'s `managers`); without it,
that case is skipped.
"""

from __future__ import annotations

import os

import httpx
import pytest
from celine.sdk.auth import JwtUser, is_platform_admin
from celine.sdk.settings.models import OidcSettings

KEYCLOAK = os.environ.get("CELINE_COMMUNITY_KEYCLOAK_URL", "").rstrip("/")
REALM = os.environ.get("CELINE_COMMUNITY_KEYCLOAK_REALM", "celine")
#: oauth2-proxy's dev secret in the local realm (celine-policies clients.yaml default).
PROXY_SECRET = os.environ.get("CELINE_COMMUNITY_OAUTH2_PROXY_SECRET", "oauth2_proxy")
LEGACY_TOKEN = os.environ.get("CELINE_COMMUNITY_LEGACY_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not KEYCLOAK, reason="CELINE_COMMUNITY_KEYCLOAK_URL not set: no local Keycloak to mint from"
)

# `celine.community` is imported inside the tests, never at collection: `test_api.py`
# pins `DEV_AUTH_ENABLED` before its first import of the settings, and a module
# collected earlier that imported them first would change that suite's posture.

REC = "example_rec"
#: A REC nobody in the dev realm belongs to.
OTHER_REC = "other_rec"


def _oidc() -> OidcSettings:
    issuer = f"{KEYCLOAK}/realms/{REALM}"
    return OidcSettings(
        base_url=issuer,
        jwks_uri=f"{issuer}/protocol/openid-connect/certs",
        audience="svc-community",
        client_id="svc-community",
    )


def _mint(username: str) -> str:
    response = httpx.post(
        f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "oauth2_proxy",
            "client_secret": PROXY_SECRET,
            "username": username,
            "password": username,
            "scope": "openid email profile organization:*",
        },
        timeout=10,
    )
    if response.status_code != 200:
        pytest.skip(f"could not mint a token for {username!r}: HTTP {response.status_code}")
    return response.json()["access_token"]


def _verified(token: str) -> JwtUser:
    return JwtUser.from_token(token, oidc=_oidc())


def _decision():
    from celine.community.security.policy import CAPABILITIES, policy

    return policy, frozenset(CAPABILITIES)


@pytest.fixture(scope="module")
def platform_admin() -> JwtUser:
    return _verified(_mint("admin"))


@pytest.fixture(scope="module")
def org_admin() -> JwtUser:
    return _verified(_mint("org-admin"))


@pytest.fixture(scope="module")
def org_viewer() -> JwtUser:
    return _verified(_mint("org-viewer"))


@pytest.fixture(scope="module")
def legacy_realm_admin() -> JwtUser:
    if not LEGACY_TOKEN:
        pytest.skip("CELINE_COMMUNITY_LEGACY_TOKEN not set: no realm-group token to test")
    user = _verified(LEGACY_TOKEN)
    claims = user.claims or {}
    assert {"/admins", "admins"} & set(claims.get("groups") or []), "not a realm-group token"
    return user


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


async def test_a_real_platform_admin_token_reaches_every_rec(platform_admin: JwtUser) -> None:
    policy, every = _decision()
    assert is_platform_admin(platform_admin.claims or {})
    assert "groups" not in (platform_admin.claims or {})
    assert (await policy.allow(platform_admin, "console.read", None)).allowed is True
    for key in (REC, OTHER_REC):
        assert await policy.capabilities(platform_admin, key) == every, key


async def test_a_real_organization_admin_is_not_a_platform_admin(org_admin: JwtUser) -> None:
    """`admins` in `example_rec` manages `example_rec` and nothing else."""
    policy, every = _decision()
    assert not is_platform_admin(org_admin.claims or {})
    assert await policy.capabilities(org_admin, REC) == every
    assert await policy.capabilities(org_admin, OTHER_REC) == frozenset()
    assert (await policy.allow(org_admin, "console.read", None)).allowed is False


async def test_a_real_organization_viewer_manages_nothing(org_viewer: JwtUser) -> None:
    policy, _ = _decision()
    assert await policy.capabilities(org_viewer, REC) == frozenset()


async def test_a_real_token_still_carrying_realm_admins_gets_its_organization_grant_only(
    legacy_realm_admin: JwtUser,
) -> None:
    """The retired realm group `/admins` (and role `admin`) grants nothing."""
    policy, _ = _decision()
    assert not is_platform_admin(legacy_realm_admin.claims or {})
    assert (await policy.allow(legacy_realm_admin, "console.read", None)).allowed is False
    assert await policy.capabilities(legacy_realm_admin, OTHER_REC) == frozenset()


# ---------------------------------------------------------------------------
# Through HTTP: the same tokens, verified by the service's own dependency
# ---------------------------------------------------------------------------


class _Registry:
    """The registry lists both RECs; it decides nothing."""

    async def list_communities(self, **kwargs):
        items = [
            type("C", (), {"key": REC, "name": "Example REC"})(),
            type("C", (), {"key": OTHER_REC, "name": "Other REC"})(),
        ]
        page = type("Page", (), {"items": items, "next_cursor": None})
        return type("Response", (), {"parsed": page(), "status_code": 200})()


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from celine.community.api.deps import get_registry_client
    from celine.community.main import create_app
    from celine.community.settings import settings

    monkeypatch.setattr(settings, "dev_auth_enabled", False)
    monkeypatch.setattr(settings, "oidc", _oidc())
    app = create_app()
    app.dependency_overrides[get_registry_client] = lambda: _Registry()
    # No lifespan, as in test_api.py: /api/me needs no database.
    yield TestClient(app)
    app.dependency_overrides.clear()


def _me(client, token: str):
    return client.get("/api/me", headers={"Authorization": f"Bearer {token}"})


def test_me_lists_every_rec_for_a_real_platform_admin(client) -> None:
    response = _me(client, _mint("admin"))
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert "platform-admin" in user["platformRoles"]
    assert {rec["key"] for rec in user["communities"]} == {REC, OTHER_REC}


def test_me_lists_only_their_own_rec_for_a_real_organization_admin(client) -> None:
    response = _me(client, _mint("org-admin"))
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert "platform-admin" not in user["platformRoles"]
    assert [rec["key"] for rec in user["communities"]] == [REC]


def test_another_recs_data_is_refused_to_a_real_organization_admin(client) -> None:
    token = _mint("org-admin")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/communities/{OTHER_REC}/members", headers=headers).status_code == 403


def test_me_refuses_a_real_token_still_carrying_realm_admins_outside_its_rec(client) -> None:
    if not LEGACY_TOKEN:
        pytest.skip("CELINE_COMMUNITY_LEGACY_TOKEN not set: no realm-group token to test")
    response = _me(client, LEGACY_TOKEN)
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert "platform-admin" not in user["platformRoles"]
    assert [rec["key"] for rec in user["communities"]] == [REC]
