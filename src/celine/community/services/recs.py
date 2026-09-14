"""Which RECs a caller may open, and what they may do in each.

The **REC registry is the REC universe**. It answers "which RECs exist" for a
realm admin and an organization-scoped manager alike: the caller's organization
aliases are *matched against* the registry rather than trusted on their own, so a
Keycloak organization aliased for a REC the registry does not know is not listed.

The registry answers enumeration and naming, never authorization. Every
`(caller, action, REC)` decision is made from the token by `policies/community.rego`
with no network call — which is why a registry outage degrades the REC *list* and
leaves per-request access untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from celine.sdk.auth import JwtUser
from celine.sdk.rec_registry import RecRegistryAdminClient

from celine.community.security.policy import policy, rec_aliases
from celine.community.settings import settings

logger = logging.getLogger(__name__)

#: One registry page is the whole list. The dashboard's REC count is the
#: deployment's REC count; a deployment that outgrows this wants a searchable
#: picker and a server-side filter, not a bigger page.
_REGISTRY_PAGE = 200


@dataclass(frozen=True)
class RecAccess:
    key: str
    name: str
    capabilities: tuple[str, ...]


class RegistryUnavailable(RuntimeError):
    """The registry could not be reached and the caller's list has no other source.

    Raised only for a caller whose grant is realm-level, because their REC list
    exists nowhere but the registry. An organization-scoped manager's list is in
    their token, so they are served from it instead.
    """


def _derived_name(key: str) -> str:
    return key.replace("-", " ").replace("_", " ").title()


async def _registry_communities(
    registry: RecRegistryAdminClient,
) -> dict[str, str] | None:
    """Every registry community as ``{key: name}``, or None if unreachable."""
    communities: dict[str, str] = {}
    cursor: str | None = None
    try:
        while True:
            response = await registry.list_communities(limit=_REGISTRY_PAGE, cursor=cursor)
            page = getattr(response, "parsed", None)
            if page is None:
                raise RuntimeError(f"REC Registry returned HTTP {response.status_code}")
            for item in page.items:
                key = getattr(item, "key", None)
                if isinstance(key, str) and key:
                    communities[key] = getattr(item, "name", None) or _derived_name(key)
            next_cursor = getattr(page, "next_cursor", None)
            if not isinstance(next_cursor, str) or not next_cursor:
                return communities
            cursor = next_cursor
    except Exception as exc:  # noqa: BLE001
        logger.warning("REC Registry community list unavailable: %s", exc)
        return None


async def accessible_recs(
    user: JwtUser,
    registry: RecRegistryAdminClient,
) -> tuple[list[RecAccess], bool]:
    """The RECs *user* holds at least one capability on, and whether the registry answered.

    Raises `RegistryUnavailable` when the registry is unreachable *and* the caller
    holds nothing at organization level — a realm admin, whose list has no other
    source. Answering 403 there would tell an administrator they have no access
    when the truth is that a downstream is down.
    """
    # A realm-level grant is organization-blind, so it is asked with no REC:
    # `granted_by_org_group` is guarded against a null organization and cannot
    # answer, leaving the realm branch as the only one that can.
    realm_wide = (await policy.allow_console(user, None)).allowed
    own = rec_aliases(user)

    registry_communities = await _registry_communities(registry)
    if registry_communities is None:
        if not own:
            raise RegistryUnavailable("the REC registry did not answer")
        candidates = {key: _derived_name(key) for key in own}
    elif realm_wide:
        candidates = registry_communities
    else:
        candidates = {key: name for key, name in registry_communities.items() if key in set(own)}

    accessible: list[RecAccess] = []
    for key, name in candidates.items():
        capabilities = offered(await policy.capabilities(user, key))
        if not capabilities:
            continue
        accessible.append(RecAccess(key=key, name=name, capabilities=tuple(sorted(capabilities))))

    accessible.sort(key=lambda rec: (rec.name.lower(), rec.key))
    return accessible, registry_communities is not None


def offered(capabilities: frozenset[str]) -> frozenset[str]:
    """The granted capabilities this deployment can honour.

    `members.invite` goes through onboarding. Without `ONBOARDING_URL` every press
    would answer `503`, so the grant is not reported and the buttons are not shown.
    The policy still decides who may press; this decides only whether pressing can
    work here.
    """
    if not settings.onboarding_url:
        return capabilities - {"members.invite"}
    return capabilities


async def has_console_access(user: JwtUser) -> bool:
    """Whether *user* may open the console at all, without asking the registry.

    Used by the session guard, which runs on every poll. `/api/me` pays for the
    registry call; a liveness check must not.
    """
    if (await policy.allow_console(user, None)).allowed:
        return True
    for alias in rec_aliases(user):
        if (await policy.allow_console(user, alias)).allowed:
            return True
    return False
