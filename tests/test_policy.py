"""Authorization tests for `policies/community.rego`.

These exercise the decision directly rather than through an endpoint, because the
cases that matter are combinations of two group levels, an organization type and a
scope — and going through HTTP would only let one of them be varied at a time.
"""

import pytest
from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import Organization

from celine.community.security.policy import CAPABILITIES, policy, rec_aliases

FULL_SCOPE = "community.read community.devices.read community.nudging.read community.alerts.write"


def user(sub: str, *, groups=(), orgs=None, scope: str = FULL_SCOPE) -> JwtUser:
    """A human caller, with the claim shape a real KC 26.4 token carries."""
    orgs = orgs or {}
    claims = {
        "sub": sub,
        "email": f"{sub}@celine.localhost",
        "preferred_username": sub,
        "groups": list(groups),
        "scope": scope,
        "organization": orgs,
    }
    return JwtUser(
        sub=sub,
        email=f"{sub}@celine.localhost",
        preferred_username=sub,
        organizations=[Organization._from_claim(alias, data) for alias, data in orgs.items()],
        claims=claims,
    )


def rec(*groups: str) -> dict:
    return {"type": ["rec"], "groups": list(groups)}


MANAGER_OF_A = user(
    "manager-a",
    orgs={"rec-a": rec("/managers"), "rec-b": rec("/participants")},
)


async def allowed(caller: JwtUser, action: str, community_key: str | None) -> bool:
    return (await policy.allow(caller, action, community_key)).allowed


# ---------------------------------------------------------------------------
# The bug this policy was rewritten to fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", CAPABILITIES)
async def test_a_manager_badge_in_one_rec_does_not_reach_another(action: str) -> None:
    """The whole point of reading the two group levels apart.

    `manager-a` holds `managers` inside rec-a and only `participants` inside
    rec-b. `extract_groups` flattens both organizations into one list, so the
    previous policy's `has_manager_role` was satisfied while asking about rec-b,
    and only a single-REC equality check stood between that and access.
    """
    assert await allowed(MANAGER_OF_A, action, "rec-a") is True
    assert await allowed(MANAGER_OF_A, action, "rec-b") is False


async def test_a_rec_the_caller_does_not_belong_to_is_denied() -> None:
    assert await allowed(MANAGER_OF_A, "community.read", "rec-z") is False


async def test_an_unknown_rec_denies_exactly_as_a_forbidden_one_does() -> None:
    """Nothing in the answer distinguishes "not yours" from "does not exist"."""
    forbidden = await policy.allow(MANAGER_OF_A, "community.read", "rec-b-but-real")
    unknown = await policy.allow(MANAGER_OF_A, "community.read", "no-such-rec")
    assert forbidden.allowed is unknown.allowed is False
    assert forbidden.reason == unknown.reason


# ---------------------------------------------------------------------------
# Which organizations, and which groups, grant anything
# ---------------------------------------------------------------------------


async def test_only_a_rec_typed_organization_grants() -> None:
    """A DSO's managers are managers of a DSO."""
    dso_manager = user("dso-manager", orgs={"set": {"type": ["dso"], "groups": ["/managers"]}})
    assert await allowed(dso_manager, "community.read", "set") is False
    assert rec_aliases(dso_manager) == []


async def test_an_untyped_organization_grants_nothing() -> None:
    """The state every organization in the realm was in before `sync-orgs` ran."""
    untyped = user("untyped", orgs={"rec-a": {"groups": ["/managers"]}})
    assert await allowed(untyped, "community.read", "rec-a") is False


@pytest.mark.parametrize("group", ["admins", "managers"])
async def test_admins_and_managers_grant(group: str) -> None:
    caller = user("member", orgs={"rec-a": rec(f"/{group}")})
    assert await allowed(caller, "community.read", "rec-a") is True


@pytest.mark.parametrize("group", ["editors", "viewers", "participants"])
async def test_no_other_realm_group_grants_anything(group: str) -> None:
    """Narrower than onboarding, deliberately: every surface here is a manager's."""
    caller = user("member", orgs={"rec-a": rec(f"/{group}")})
    assert await policy.capabilities(caller, "rec-a") == frozenset()
    assert await policy.capabilities(user("member", groups=[f"/{group}"]), "rec-a") == frozenset()


async def test_the_group_names_the_realm_actually_has_are_the_ones_that_work() -> None:
    """`rec-manager`, `rec-managers`, `manager`, `admin` matched no real group."""
    for spelling in ("manager", "rec-manager", "rec-managers", "admin"):
        caller = user("member", orgs={"rec-a": rec(f"/{spelling}")})
        assert await allowed(caller, "community.read", "rec-a") is False, spelling


# ---------------------------------------------------------------------------
# The realm-level grant
# ---------------------------------------------------------------------------


async def test_a_realm_group_reaches_every_rec_including_ones_it_is_not_in() -> None:
    admin = user("platform-admin", groups=["/admins"])
    assert await allowed(admin, "community.read", "rec-a") is True
    assert await allowed(admin, "community.read", "rec-z") is True


async def test_a_realm_grant_answers_with_no_rec_named() -> None:
    """How "may this caller open the console at all" is asked.

    The organization branch is guarded against a null organization, so a caller
    with no realm group cannot be granted by a null-versus-null comparison.
    """
    assert await allowed(user("platform-admin", groups=["/admins"]), "console.read", None) is True
    assert await allowed(MANAGER_OF_A, "console.read", None) is False


# ---------------------------------------------------------------------------
# Scopes, which are orthogonal to groups
# ---------------------------------------------------------------------------


async def test_a_manager_without_the_surface_scope_is_still_denied_that_surface() -> None:
    narrow = user("narrow", orgs={"rec-a": rec("/managers")}, scope="community.read")
    capabilities = await policy.capabilities(narrow, "rec-a")
    assert "community.read" in capabilities
    assert "alerts.read" in capabilities
    assert "devices.read" not in capabilities
    assert "nudging.read" not in capabilities
    assert "alerts.write" not in capabilities


async def test_the_scope_requirement_applies_to_a_realm_admin_too() -> None:
    narrow_admin = user("narrow-admin", groups=["/admins"], scope="community.read")
    assert await allowed(narrow_admin, "devices.read", "rec-a") is False


# ---------------------------------------------------------------------------
# Service accounts
# ---------------------------------------------------------------------------


def service(scope: str) -> JwtUser:
    claims = {
        "sub": "svc",
        "preferred_username": "service-account-svc-community",
        "scope": scope,
    }
    return JwtUser(sub="svc", preferred_username="service-account-svc-community", claims=claims)


async def test_a_service_is_authorized_by_scope_and_reaches_every_rec() -> None:
    svc = service("community.read")
    assert await allowed(svc, "community.read", "rec-a") is True
    assert await allowed(svc, "community.read", "rec-z") is True
    assert await allowed(svc, "alerts.write", "rec-a") is False


async def test_community_admin_is_a_service_scope_and_grants_no_human() -> None:
    """It used to be a bare rule, so minting it onto a user was a total bypass."""
    assert await allowed(service("community.admin"), "alerts.write", "rec-a") is True
    impostor = user("impostor", orgs={"rec-a": rec("/participants")}, scope="community.admin")
    assert await allowed(impostor, "alerts.write", "rec-a") is False


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


async def test_an_action_with_no_capability_declared_is_denied() -> None:
    decision = await policy.allow(MANAGER_OF_A, "exports.purge", "rec-a")
    assert decision.allowed is False
    assert "unknown action" in (decision.reason or "")


# ---------------------------------------------------------------------------
# The members surface: names on a screen, and an email that follows a press
# ---------------------------------------------------------------------------

MEMBER_ACTIONS = ("members.read", "members.invite")


@pytest.mark.parametrize("action", MEMBER_ACTIONS)
async def test_a_manager_reaches_the_members_of_their_own_rec_only(action: str) -> None:
    assert await allowed(MANAGER_OF_A, action, "rec-a") is True
    assert await allowed(MANAGER_OF_A, action, "rec-b") is False


@pytest.mark.parametrize("action", MEMBER_ACTIONS)
async def test_a_realm_admin_reaches_the_members_of_every_rec(action: str) -> None:
    admin = user("platform-admin", groups=["/admins"])
    assert await allowed(admin, action, "rec-a") is True
    assert await allowed(admin, action, "rec-b") is True


@pytest.mark.parametrize("action", MEMBER_ACTIONS)
async def test_a_viewer_of_the_rec_does_not_reach_its_members(action: str) -> None:
    viewer = user("viewer", orgs={"rec-a": rec("/viewers")})
    assert await allowed(viewer, action, "rec-a") is False


@pytest.mark.parametrize("action", MEMBER_ACTIONS)
async def test_being_a_manager_is_enough_with_no_surface_scope(action: str) -> None:
    """Requester, 2026-09-14, A4: no human scope on top of the group."""
    bare = user("bare", orgs={"rec-a": rec("/managers")}, scope="")
    assert await allowed(bare, action, "rec-a") is True


@pytest.mark.parametrize("action", MEMBER_ACTIONS)
async def test_no_service_scope_reaches_the_members_not_even_community_admin(action: str) -> None:
    """Names are for a person's screen, and an email follows a person's decision."""
    for scope in ("community.admin", "community.read", f"community.{action}"):
        decision = await policy.allow(service(scope), action, "rec-a")
        assert decision.allowed is False, scope
        assert decision.reason == "only a person may perform this action, never a service"
    # The superset still grants everything else.
    assert await allowed(service("community.admin"), "alerts.write", "rec-a") is True


class _OneRecRegistry:
    async def list_communities(self, **kwargs):
        page = type("Page", (), {"items": [type("C", (), {"key": "rec-a", "name": "REC A"})()]})
        return type("Response", (), {"parsed": page(), "status_code": 200})()


async def test_me_offers_members_invite_only_when_onboarding_is_configured(monkeypatch) -> None:
    """Without `ONBOARDING_URL` every press would be a 503, so the buttons are not offered."""
    from celine.community.services.recs import accessible_recs
    from celine.community.settings import settings

    monkeypatch.setattr(settings, "onboarding_url", None)
    recs, _ = await accessible_recs(MANAGER_OF_A, _OneRecRegistry())
    assert "members.read" in recs[0].capabilities
    assert "members.invite" not in recs[0].capabilities

    monkeypatch.setattr(settings, "onboarding_url", "http://onboarding.internal")
    recs, _ = await accessible_recs(MANAGER_OF_A, _OneRecRegistry())
    assert "members.invite" in recs[0].capabilities
