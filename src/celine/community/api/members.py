"""The REC's members, by name, for the manager who sends them an invitation.

Names are read from the REC registry on every request and never kept: no database
write, no cache, and no name in a log line, which names the member key instead.
Requester, 2026-09-14 (A1): "needed or the manager won't be able to use it".

Only `key`, `name`, `role`, `status`, `area`, `hasMeter` and `hasDeliveryPoint`
leave this module. The registry's list item also carries `user_id` (often the
member's email address) and `did`, and neither is needed to find a person and press
a button.

`hasDeliveryPoint` is yes or no, never which delivery point (ADR-0005): it is the
registry's delivery point count, greater than zero. The POD itself appears only in
the measurements dialog (`member_meter`).

`hasMeter` is yes or no, never which meter (ADR-0004). It comes from the
community's meter list, of which only the owner keys are kept: the sensor ids are
dropped as the page is read, and never logged.
"""

import logging

from celine.sdk.openapi.rec_registry.errors import UnexpectedStatus
from fastapi import APIRouter, HTTPException, Query

from celine.community.api.deps import MembersReadDep, RegistryDep
from celine.community.api.schemas import MembersResponse, MemberSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communities/{community_key}/members", tags=["members"])

#: The registry's own ceiling (`rec-registry` `max_page_size`). A larger page is
#: its `422`, which would read here as the registry being down.
MAX_PAGE = 500


def shown_name(key: str, name: str | None) -> str | None:
    """The registry's name, or None when it only repeats the key.

    Onboarding writes the submission reference as the name when a person gave
    none, so a name equal to the key tells the manager nothing the key column
    does not. Placeholders from a bundle import (`Participant EX-00001`) are a
    name as far as anyone here can tell, and are shown as they are.
    """
    if not name or not name.strip():
        return None
    if name.strip().casefold() == key.strip().casefold():
        return None
    return name


#: How many registry pages of meters the flag may read. A REC with more meters than
#: this shows the flag as unknown rather than holding the request open.
_METER_PAGES = 10


async def meter_holders(registry, community_key: str) -> set[str] | None:
    """The keys of the members who hold a meter, or None when it cannot be told.

    Only `owner_key` is kept from each item: the sensor id never leaves this loop.
    """
    holders: set[str] = set()
    cursor: str | None = None
    try:
        for _ in range(_METER_PAGES):
            response = await registry.list_meters(community_key, limit=MAX_PAGE, cursor=cursor)
            page = getattr(response, "parsed", None)
            items = getattr(page, "items", None)
            if items is None:
                raise RuntimeError(f"REC Registry returned HTTP {response.status_code}")
            holders.update(
                owner
                for item in items
                if isinstance(owner := getattr(item, "owner_key", None), str)
            )
            next_cursor = getattr(page, "next_cursor", None)
            if not isinstance(next_cursor, str) or not next_cursor:
                return holders
            cursor = next_cursor
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "REC Registry meters unavailable for the members list community=%s error=%s",
            community_key,
            type(exc).__name__,
        )
        return None
    logger.warning(
        "REC Registry meters exceed %s pages; meter flag unknown community=%s",
        _METER_PAGES,
        community_key,
    )
    return None


def has_delivery_point(item) -> bool | None:
    """Whether the registry counts a delivery point for the member; None when it says nothing."""
    count = getattr(item, "delivery_points_count", None)
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return count > 0


def _matches(member: MemberSummary, needle: str) -> bool:
    return needle in member.key.casefold() or (
        member.name is not None and needle in member.name.casefold()
    )


@router.get("", response_model=MembersResponse)
async def members(
    community_key: str,
    user: MembersReadDep,
    registry: RegistryDep,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=50),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
) -> MembersResponse:
    """One registry page of members, optionally narrowed by name or key.

    `q` filters **inside this page**: the registry's `list_members` has no text
    filter. `nextCursor` is the registry's, unchanged, so the dashboard can load
    the next page and filter that too.
    """
    try:
        response = await registry.list_members(
            community_key,
            status=status or None,
            limit=limit,
            cursor=cursor or None,
        )
    except UnexpectedStatus as exc:
        # Not `str(exc)`: it carries the response body, and this is the one module
        # whose responses hold participant names.
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail={"code": "community_not_found"}) from exc
        logger.warning(
            "REC Registry members unavailable community=%s status=%s",
            community_key,
            exc.status_code,
        )
        raise HTTPException(status_code=503, detail={"code": "registry_unavailable"}) from exc
    except Exception as exc:
        logger.warning(
            "REC Registry members unavailable community=%s error=%s",
            community_key,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail={"code": "registry_unavailable"}) from exc

    page = getattr(response, "parsed", None)
    items = getattr(page, "items", None)
    if items is None:
        logger.warning(
            "REC Registry members unreadable community=%s status=%s",
            community_key,
            getattr(response, "status_code", None),
        )
        raise HTTPException(status_code=503, detail={"code": "registry_unavailable"})

    holders = await meter_holders(registry, community_key) if items else set()
    summaries = [
        MemberSummary(
            key=item.key,
            name=shown_name(item.key, item.name),
            role=item.role,
            status=item.status,
            area=item.area,
            has_meter=None if holders is None else item.key in holders,
            has_delivery_point=has_delivery_point(item),
        )
        for item in items
    ]
    if q and q.strip():
        needle = q.strip().casefold()
        summaries = [member for member in summaries if _matches(member, needle)]

    next_cursor = getattr(page, "next_cursor", None)
    return MembersResponse(
        community_key=community_key,
        items=summaries,
        next_cursor=next_cursor if isinstance(next_cursor, str) and next_cursor else None,
    )
