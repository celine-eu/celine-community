"""The REC's members, by name, for the manager who sends them an invitation.

Names are read from the REC registry on every request and never kept: no database
write, no cache, and no name in a log line, which names the member key instead.
Requester, 2026-09-14 (A1): "needed or the manager won't be able to use it".

Only `key`, `name`, `role`, `status` and `area` leave this module. The registry's
list item also carries `user_id` (often the member's email address), `did` and a
delivery point count, and none of them is needed to find a person and press a
button.
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


def _shown_name(key: str, name: str | None) -> str | None:
    """The registry's name, or None when it only repeats the key.

    Onboarding writes the submission reference as the name when a person gave
    none, so a name equal to the key tells the manager nothing the key column
    does not. Placeholders from a bundle import (`Participant GL-00001`) are a
    name as far as anyone here can tell, and are shown as they are.
    """
    if not name or not name.strip():
        return None
    if name.strip().casefold() == key.strip().casefold():
        return None
    return name


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

    summaries = [
        MemberSummary(
            key=item.key,
            name=_shown_name(item.key, item.name),
            role=item.role,
            status=item.status,
            area=item.area,
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
