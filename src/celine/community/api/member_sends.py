"""Every invitation and password reset sent from this dashboard, filterable.

Requester, 2026-09-14 (A5): the alerts audit feed already shows these rows, and a
dedicated view is worth having because it is cheap. It reads the rows
`member_emails.py` writes and nothing else.

The rows hold member **keys**. Names are resolved at read time from the REC
registry, as on the members list, and are not stored. When the registry does not
answer, the rows still come back, with `names_available: false` and no names:
who pressed what, and what happened, does not depend on the registry.
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, or_, select

from celine.community.api.deps import DbDep, MembersReadDep, RegistryDep
from celine.community.api.member_emails import AUDIT_ACTIONS, Intent
from celine.community.api.members import MAX_PAGE, shown_name
from celine.community.api.schemas import MemberSend, MemberSendsResponse
from celine.community.db.models import AuditEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communities/{community_key}/members/sends", tags=["members"])

INTENTS: dict[str, str] = {action: intent for intent, action in AUDIT_ACTIONS.items()}

#: How many registry pages a name lookup may read. A REC larger than this many
#: pages shows keys without names rather than holding the request open.
_NAME_PAGES = 10


def _cursor(row: AuditEvent) -> str:
    return f"{row.created_at.isoformat()}|{row.id}"


def _parse_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        created_at, row_id = value.rsplit("|", 1)
        return datetime.fromisoformat(created_at), UUID(row_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from exc


async def _names(registry, community_key: str, keys: set[str]) -> dict[str, str | None] | None:
    """Registry names for *keys*, or None when the registry did not answer."""
    found: dict[str, str | None] = {}
    if not keys:
        return found
    cursor: str | None = None
    try:
        for _ in range(_NAME_PAGES):
            response = await registry.list_members(community_key, limit=MAX_PAGE, cursor=cursor)
            page = getattr(response, "parsed", None)
            items = getattr(page, "items", None)
            if items is None:
                raise RuntimeError(f"REC Registry returned HTTP {response.status_code}")
            for item in items:
                if item.key in keys:
                    found[item.key] = shown_name(item.key, item.name)
            next_cursor = getattr(page, "next_cursor", None)
            if keys <= found.keys() or not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "REC Registry names unavailable for sends community=%s error=%s",
            community_key,
            type(exc).__name__,
        )
        return None
    return found


@router.get("", response_model=MemberSendsResponse)
async def member_sends(
    community_key: str,
    user: MembersReadDep,
    db: DbDep,
    registry: RegistryDep,
    member_key: str | None = Query(default=None, max_length=100),
    actor: str | None = Query(default=None, max_length=255),
    intent: Intent | None = None,
    code: str | None = Query(default=None, max_length=64),
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemberSendsResponse:
    """Newest first. `code` narrows **inside** the page, like `q` on the list.

    Everything else filters in SQL. `code` lives in the row's JSON `detail`, and
    filtering it here needs no migration; `nextCursor` still follows the SQL page.
    """
    actions = [AUDIT_ACTIONS[intent]] if intent else list(AUDIT_ACTIONS.values())
    query = (
        select(AuditEvent)
        .where(AuditEvent.community_key == community_key)
        .where(AuditEvent.action.in_(actions))
    )
    if member_key:
        query = query.where(AuditEvent.resource_id == member_key)
    if actor:
        query = query.where(AuditEvent.actor_id == actor)
    if from_:
        query = query.where(AuditEvent.created_at >= from_)
    if to:
        query = query.where(AuditEvent.created_at < to)
    if cursor:
        created_at, row_id = _parse_cursor(cursor)
        query = query.where(
            or_(
                AuditEvent.created_at < created_at,
                and_(AuditEvent.created_at == created_at, AuditEvent.id < row_id),
            )
        )
    query = query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1)

    rows = list((await db.execute(query)).scalars())
    next_cursor = _cursor(rows[limit - 1]) if len(rows) > limit else None
    rows = rows[:limit]
    if code:
        rows = [row for row in rows if str((row.detail or {}).get("code")) == code]

    names = await _names(
        registry, community_key, {row.resource_id for row in rows if row.resource_id}
    )
    items = [
        MemberSend(
            id=row.id,
            created_at=row.created_at,
            member_key=row.resource_id or "",
            member_name=(names or {}).get(row.resource_id or ""),
            intent=INTENTS[row.action],
            code=str((row.detail or {}).get("code", "")),
            actor_id=row.actor_id,
        )
        for row in rows
    ]
    return MemberSendsResponse(
        community_key=community_key,
        items=items,
        next_cursor=next_cursor,
        names_available=names is not None,
    )
