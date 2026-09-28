"""A manager corrects a member's role and area (ADR-0003), and reads the REC's areas.

    dashboard ─▶ these routes ─▶ REC registry

The registry is written directly, with this BFF's own token, through its dedicated
profile route (`PATCH …/members/{member_key}/profile`, `{role?, area?}` and
nothing else). A token asked for `rec-registry.members.profile.write` is used for
that write and for nothing else; the member and the REC's areas are read with the
default `rec-registry.read` token, which is the one the Digital Twin receives too.

**The dashboard moves a role between `consumer` and `prosumer` only.** Settlement
counts a meter's production only for a `prosumer`. A request for any other role,
and a role change for a member whose role is neither (`producer`, an imported
`operator` or `admin`), is refused here, before any registry write; the area of
every member stays editable. The first refusal needs no registry call at all.

**The registry decides what is valid; this BFF decides who may press.** The area
is checked against the REC's areas before the write so the manager gets the
answer without a write token being minted, and the registry checks it again:
its `unknown_area` and `invalid_role` reach the dashboard unchanged. Whether the
caller may press on this REC is `members.edit` in `policies/community.rego`.
No cache and no retry.

**Every press that reaches the registry writes one audit row**
(`community.member.profile`, resource `registry_member`, the member key,
`{code, status, changed}` and, per changed field, `{from, to}`), refusals and
no-ops included. A refused press also records `attempted`: `{from, to}` for each
field it asked to change, beside `changed: []`.

**Only an active member's role and area are edited.** A member whose registry
status is anything else (`pending`, `suspended`, `inactive`) is refused `409
member_not_active` after the member is read and before any write token is asked
for; the dashboard shows the members' status and offers no edit for them. A role and an area key are administrative values, not personal
data; no name, email or address is in the row or in a log line. The "Sent
emails" view reads invitation and password-reset rows only, so these never
appear there.
"""

import logging
from typing import Annotated, Any

import httpx
from celine.sdk.auth import JwtUser
from celine.sdk.rec_registry import RecRegistryApiError
from fastapi import APIRouter, HTTPException, Path

from celine.community.api.deps import (
    CommunityReadDep,
    DbDep,
    MembersEditDep,
    RegistryDep,
    RegistryProfileWriterDep,
)
from celine.community.api.registry_press import (
    Outcome,
    Refused,
    audit,
    is_active,
    log_upstream,
    read,
    text,
)
from celine.community.api.schemas import (
    EDITABLE_ROLES,
    AreaBoundary,
    CommunityArea,
    CommunityAreas,
    MemberProfileEdit,
    MemberProfileEdited,
)
from celine.community.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communities/{community_key}", tags=["members"])

AUDIT_ACTION = "community.member.profile"

MemberKey = Annotated[str, Path(min_length=1, max_length=100)]

#: Registry codes passed through with the registry's status.
_PASSED_THROUGH: dict[str, int] = {
    "invalid_role": 422,
    "unknown_area": 422,
    "member_not_found": 404,
    "community_not_found": 404,
}


# ---------------------------------------------------------------------------
# The REC's areas
# ---------------------------------------------------------------------------


def _boundary(area: Any) -> AreaBoundary | None:
    """The area's `boundary: {source, id}`, when the registry records one.

    The registry's area carries none until areas reference primary-substation
    boundaries; the value is read wherever the client puts it (a typed field or
    an unknown key) so that it shows as soon as the registry answers it.
    """
    value = getattr(area, "boundary", None)
    if value is None:
        extra = getattr(area, "additional_properties", None)
        value = extra.get("boundary") if isinstance(extra, dict) else None
    if value is not None and not isinstance(value, dict):
        to_dict = getattr(value, "to_dict", None)
        value = to_dict() if callable(to_dict) else None
    if not isinstance(value, dict):
        return None
    source, boundary_id = value.get("source"), value.get("id")
    if isinstance(source, str) and source and isinstance(boundary_id, str) and boundary_id:
        return AreaBoundary(source=source, id=boundary_id)
    return None


def _primary_substation(area: Any) -> str | None:
    """The first topology node id of the area: the primary substation the pipelines
    attribute the area's members to (`topology_ids[1]` in the mirror).

    Once an area references a boundary it lists exactly one node, whose id is the
    boundary id (the registry's area invariant), so the two agree.
    """
    topology = getattr(area, "topology", None)
    if not isinstance(topology, list):
        extra = getattr(area, "additional_properties", None)
        topology = extra.get("topology") if isinstance(extra, dict) else None
    if not isinstance(topology, list):
        return None
    first = next((node for node in topology if isinstance(node, str) and node.strip()), None)
    return first.strip() if first else None


def community_areas(community: Any) -> list[CommunityArea]:
    """The areas of a registry `CommunityDetail`, by key, in key order."""
    areas = getattr(community, "areas", None)
    entries = getattr(areas, "additional_properties", None)
    if not isinstance(entries, dict):
        return []
    result: list[CommunityArea] = []
    for key in sorted(entries):
        area = entries[key]
        name = getattr(area, "name", None)
        result.append(
            CommunityArea(
                key=key,
                name=name if isinstance(name, str) and name.strip() else key,
                boundary=_boundary(area),
                primary_substation=_primary_substation(area),
            )
        )
    return result


async def _read_areas(registry, community_key: str, member_key: str) -> list[CommunityArea]:
    community = await read(
        registry.get_community(community_key),
        "community",
        community_key,
        member_key,
        missing="community_not_found",
    )
    return community_areas(community)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def _write_refused(exc: Exception, community_key: str, member_key: str) -> Refused:
    """How a refused registry write reads to the manager."""
    if isinstance(exc, RecRegistryApiError):
        status = exc.status_code
        # `exc.code` and the status only: the registry's sentence is not logged.
        log_upstream("profile write", community_key, member_key, status, exc.code)
        if exc.code in _PASSED_THROUGH:
            return Refused(_PASSED_THROUGH[exc.code], exc.code, status)
        if status in (401, 403):
            # A missing grant is a deployment fault. A 403 here would tell the
            # manager *they* were refused.
            return Refused(502, "registry_refused", status)
        if status == 422:
            return Refused(422, exc.code or "profile_rejected", status)
        return Refused(502, "registry_unavailable", status)
    if isinstance(exc, httpx.HTTPStatusError):
        # Only the token provider raises this: Keycloak refusing the write token,
        # most often `invalid_scope` on a realm where svc-community does not hold
        # the optional scope.
        logger.error(
            "Keycloak refused this BFF's registry profile token community=%s member=%s "
            "status=%s (is %s an optional scope of the client on this realm?)",
            community_key,
            member_key,
            exc.response.status_code,
            settings.rec_registry_profile_write_scope,
        )
        return Refused(502, "registry_refused", None)
    logger.warning(
        "REC Registry profile write unavailable community=%s member=%s error=%s",
        community_key,
        member_key,
        type(exc).__name__,
    )
    return Refused(502, "registry_unavailable", None)


def _stripped(value: str | None) -> str | None:
    return None if value is None else value.strip()


def _attempted(
    role: str | None,
    area: str | None,
    current_role: str | None,
    current_area: str | None,
    *,
    known: bool,
) -> dict[str, dict[str, str | None]]:
    """What a refused press asked to change, as `{field: {from, to}}`.

    A field asked with the value the member already has is left out. When the
    member could not be read, `from` is unknown (`None`) and every asked field is
    listed. Role and area keys only: no personal data.
    """
    attempted: dict[str, dict[str, str | None]] = {}
    if role is not None and (not known or role != (current_role or "").strip().casefold()):
        attempted["role"] = {"from": current_role, "to": role}
    if area is not None and (not known or area != current_area):
        attempted["area"] = {"from": current_area, "to": area}
    return attempted


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/areas", response_model=CommunityAreas)
async def areas(
    community_key: str,
    user: CommunityReadDep,
    registry: RegistryDep,
) -> CommunityAreas:
    """The REC's areas: key, name, the boundary each references when there is one,
    and its primary substation (the first topology node id).

    What the member edit dialog's area select offers. Read from the registry on
    every request, with the default token; nothing here writes an area. `404
    community_not_found`, `502 registry_unavailable`, `502 registry_refused`.
    """
    try:
        found = await _read_areas(registry, community_key, "-")
    except Refused as refused:
        raise HTTPException(status_code=refused.status, detail={"code": refused.code}) from None
    return CommunityAreas(community_key=community_key, areas=found)


@router.patch("/members/{member_key}", response_model=MemberProfileEdited)
async def edit_member_profile(
    community_key: str,
    member_key: MemberKey,
    body: MemberProfileEdit,
    user: MembersEditDep,
    registry: RegistryDep,
    writer: RegistryProfileWriterDep,
    db: DbDep,
) -> MemberProfileEdited:
    """Correct the member's role, area, or both.

    `{role?, area?}`, at least one. `200 {outcome: "updated", memberKey, role,
    area, changed}` when the registry wrote the change; `200` with `outcome:
    "unchanged"` and `changed: []` when the member already has what was asked,
    and nothing is written. Refusals are `{"detail": {"code"}}`:

    - before the registry is asked anything, and with no audit row: `422
      profile_empty` (neither key), `422 role_not_allowed` (a role other than
      `consumer` or `prosumer`), `422 unknown_area` (a blank area);
    - after the member is read: `409 member_not_active` (the member's status is
      not `active`; ADR-0004), `409 role_read_only` (a role change for a member
      whose role is neither `consumer` nor `prosumer`), `422 unknown_area` (not
      one of this REC's areas), `404 member_not_found`, `404
      community_not_found`;
    - from the write: the registry's `422 invalid_role` and `422 unknown_area`,
      `404 member_not_found`, `404 community_not_found`;
    - `502 registry_unavailable`, `502 registry_refused` (the registry or
      Keycloak refused this BFF's grant).
    """
    role = _stripped(body.role)
    area = _stripped(body.area)
    if role is None and area is None:
        raise HTTPException(status_code=422, detail={"code": "profile_empty"})
    if role is not None:
        role = role.casefold()
        if role not in EDITABLE_ROLES:
            raise HTTPException(status_code=422, detail={"code": "role_not_allowed"})
    if area is not None and not area:
        raise HTTPException(status_code=422, detail={"code": "unknown_area"})

    changes: dict[str, str] = {}
    current_role: str | None = None
    current_area: str | None = None
    member_read = False
    try:
        member = await read(
            registry.get_member(community_key, member_key),
            "member",
            community_key,
            member_key,
            missing="member_not_found",
        )
        member_read = True
        current_role = text(getattr(member, "role", None))
        current_area = text(getattr(member, "area", None))

        if not is_active(member):
            # Only an active member's role and area are corrected here; a detach
            # stays open for every member (member_meter).
            raise Refused(409, "member_not_active", 200)

        if role is not None and role != (current_role or "").strip().casefold():
            if (current_role or "").strip().casefold() not in EDITABLE_ROLES:
                raise Refused(409, "role_read_only", 200)
            changes["role"] = role
        if area is not None and area != current_area:
            if area not in {a.key for a in await _read_areas(registry, community_key, member_key)}:
                raise Refused(422, "unknown_area", 200)
            changes["area"] = area

        if not changes:
            outcome = Outcome(200, "unchanged", 200)
            new_role, new_area = current_role, current_area
        else:
            try:
                updated = await writer.patch_member_profile(
                    community_key,
                    member_key,
                    role=changes.get("role"),
                    area=changes.get("area"),
                )
            except Exception as exc:  # noqa: BLE001
                raise _write_refused(exc, community_key, member_key) from None
            outcome = Outcome(200, "updated", 200)
            new_role, new_area = text(updated.role), text(updated.area)
    except Refused as refused:
        outcome = Outcome(refused.status, refused.code, refused.upstream)
        changes = {}

    detail: dict[str, Any] = {
        "code": outcome.code,
        "status": outcome.upstream,
        "changed": sorted(changes),
    }
    if "role" in changes:
        detail["role"] = {"from": current_role, "to": changes["role"]}
    if "area" in changes:
        detail["area"] = {"from": current_area, "to": changes["area"]}
    if outcome.status >= 400:
        detail["attempted"] = _attempted(role, area, current_role, current_area, known=member_read)
    await audit(db, community_key, member_key, user, AUDIT_ACTION, detail)
    _log(community_key, member_key, user, outcome, sorted(changes))

    if outcome.status >= 400:
        raise HTTPException(status_code=outcome.status, detail={"code": outcome.code})
    return MemberProfileEdited(
        outcome=outcome.code,
        member_key=member_key,
        role=new_role or "",
        area=new_area or "",
        changed=sorted(changes),
    )


def _log(
    community_key: str, member_key: str, user: JwtUser, outcome: Outcome, changed: list[str]
) -> None:
    logger.info(
        "Member profile community=%s member=%s code=%s status=%s changed=%s actor=%s",
        community_key,
        member_key,
        outcome.code,
        outcome.status,
        ",".join(changed) or "-",
        user.sub,
    )
