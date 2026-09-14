"""Local OPA policy evaluation for the Community Manager BFF.

Wraps `policies/community.rego`. The wrapper's one job beyond plumbing is to keep
realm-level and organization-level groups **apart**, and to pass only the
organization matching the REC being asked about. See `policies/community.rego`
for why that is load-bearing rather than tidy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import organization_aliases, organization_groups, realm_groups

from celine.community.settings import settings

logger = logging.getLogger(__name__)

_PACKAGE = "celine.community.access"

#: The organization attribute value that marks a Keycloak organization as a REC.
#: One spelling, not three: `celine-policies keycloak sync-orgs` writes
#: `attributes.type = [organization.role]` from the owners registry, where the
#: vocabulary is `rec` / `dso` / `org`. A second accepted spelling would be a
#: second place for that vocabulary to be decided.
REC_ORG_TYPE = "rec"

#: Every action the rego declares a capability for. `capabilities()` evaluates
#: all of them per REC, which is what lets the UI hide what the caller cannot do.
#: An action missing here is simply never offered; an action missing from the
#: rego is denied.
CAPABILITIES: tuple[str, ...] = (
    "console.read",
    "community.read",
    "objectives.write",
    "devices.read",
    "flexibility.read",
    "gamification.read",
    "nudging.read",
    "alerts.read",
    "alerts.write",
    "members.read",
    "members.invite",
)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None


def rec_aliases(user: JwtUser) -> list[str]:
    """The caller's organizations that are RECs, by alias.

    The alias is the identity: it is the REC registry's community key, the Digital
    Twin network id, and what `celine-grid` filters on. The Keycloak UUID is
    parsed by the SDK and deliberately unused — nothing downstream keys on it.

    An organization with no `type` is not a REC. Before
    `celine-policies keycloak sync-orgs` ran with the community in the owners
    registry, *every* organization in the realm answered that way, which is the
    condition this returning empty is reporting.
    """
    return [
        org.alias
        for org in user.organizations
        if (org.type or "").lower() == REC_ORG_TYPE and org.alias
    ]


def _policy_input(user: JwtUser, action: str, community_key: str | None):
    from celine.sdk.policies import (
        Action,
        PolicyInput,
        Resource,
        ResourceType,
        Subject,
        SubjectType,
    )

    claims = user.claims or {}
    aliases = organization_aliases(claims)
    realm = realm_groups(claims)

    # Organization or realm membership is the authoritative "this is a human"
    # signal. `is_service_account` can misfire on a user JWT that carries a
    # `scope` claim but no groups — the trap celine-grid and onboarding both
    # document.
    if aliases or realm:
        subject_type = SubjectType.USER
    elif user.is_service_account:
        subject_type = SubjectType.SERVICE
    else:
        subject_type = SubjectType.USER

    raw_scope = claims.get("scope") or ""
    scopes = raw_scope.split() if isinstance(raw_scope, str) else list(raw_scope)

    # Only the organization matching *this* request is passed through, with that
    # organization's groups and type. The rego therefore cannot compare a group
    # held in one REC against another REC.
    matched = community_key if community_key and community_key in aliases else None
    org = user.get_organization(matched) if matched else None

    return PolicyInput(
        subject=Subject(
            id=user.sub,
            type=subject_type,
            groups=realm,
            scopes=scopes,
            claims={
                "organization": matched,
                "org_groups": organization_groups(claims, matched) if matched else [],
                "org_type": (org.type or "").lower() if org and org.type else None,
            },
        ),
        resource=Resource(
            # USERDATA is a generic stand-in: community.rego inspects only
            # resource.attributes, and the SDK's ResourceType enum has no member
            # for a REC.
            type=ResourceType.USERDATA,
            id=f"community/{community_key or '-'}",
            attributes={"community_key": community_key},
        ),
        action=Action(name=action),
    )


class CommunityAccessPolicy:
    def __init__(self) -> None:
        self._engine = None
        try:
            from celine.sdk.policies import PolicyEngine

            policies_dir = settings.policies.policies_dir
            self._engine = PolicyEngine(policies_dir=str(policies_dir))
            self._engine.load()
            logger.info("Community access policy loaded from %s", policies_dir)
        except Exception as exc:
            if settings.environment == "production":
                raise RuntimeError("Community policies failed to load") from exc
            logger.warning("Community policy unavailable in development: %s", exc)

    async def _evaluate(self, user: JwtUser, action: str, community_key: str | None) -> Decision:
        if self._engine is None:
            return Decision(settings.dev_auth_enabled, "development policy fallback")
        try:
            result = self._engine.evaluate_decision(
                _PACKAGE,
                _policy_input(user, action, community_key),
            )
            return Decision(result.allowed, result.reason or None)
        except Exception as exc:
            logger.exception("Policy evaluation failed")
            if settings.environment == "production":
                return Decision(False, "policy evaluation failed")
            return Decision(settings.dev_auth_enabled, f"development policy fallback: {exc}")

    async def allow(self, user: JwtUser, action: str, community_key: str | None) -> Decision:
        return await self._evaluate(user, action, community_key)

    async def capabilities(self, user: JwtUser, community_key: str | None) -> frozenset[str]:
        """Every action *user* may perform on *community_key*.

        Resolved per REC rather than once: the whole point of the organization
        check is that the answer differs between RECs. That is `len(CAPABILITIES)`
        in-process evaluations per REC, against a REC count in the single digits
        for an org-scoped manager and the registry's count for a realm admin.
        """
        allowed: set[str] = set()
        for action in CAPABILITIES:
            if (await self._evaluate(user, action, community_key)).allowed:
                allowed.add(action)
        return frozenset(allowed)

    async def allow_console(self, user: JwtUser, community_key: str | None) -> Decision:
        return await self._evaluate(user, "console.read", community_key)

    async def allow_community_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "community.read", community_key)

    async def allow_objectives_write(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "objectives.write", community_key)

    async def allow_devices_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "devices.read", community_key)

    async def allow_flexibility_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "flexibility.read", community_key)

    async def allow_gamification_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "gamification.read", community_key)

    async def allow_nudging_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "nudging.read", community_key)

    async def allow_alerts_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "alerts.read", community_key)

    async def allow_alerts_write(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "alerts.write", community_key)

    async def allow_members_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "members.read", community_key)

    async def allow_members_invite(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "members.invite", community_key)


policy = CommunityAccessPolicy()
