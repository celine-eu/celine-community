"""Authenticated feedback collection for the manager dashboard."""

import base64
import binascii
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select

from celine.community.api.deps import (
    CommunityReadDep,
    ConsoleUserDep,
    DbDep,
    UserFeedbackDep,
)
from celine.community.api.schemas import (
    FeedbackCreateRequest,
    FeedbackCreateResponse,
    FeedbackItemResponse,
    FeedbackListResponse,
    FeedbackState,
    FeedbackStatusCounts,
    FeedbackStatusUpdate,
)
from celine.community.db.models import AuditEvent, FeedbackEntry
from celine.community.security.policy import policy
from celine.community.services.user_feedback import UserFeedbackError

router = APIRouter(tags=["feedback"])
_STATUS_ORDER = {"new": 0, "seen": 1, "resolved": 2}
_SAFE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else None


def _response(entry: FeedbackEntry) -> FeedbackItemResponse:
    return FeedbackItemResponse(
        id=entry.id,
        rating=entry.rating,
        comment=entry.comment,
        page_url=entry.page_url,
        page_title=entry.page_title,
        page_path=entry.page_path,
        locale=entry.locale,
        timezone=entry.timezone,
        viewport_width=entry.viewport_width,
        viewport_height=entry.viewport_height,
        screen_width=entry.screen_width,
        screen_height=entry.screen_height,
        color_scheme=entry.color_scheme,
        client_timestamp=entry.client_timestamp,
        extra=entry.extra_context or {},
        has_screenshot=entry.screenshot_bytes is not None,
        status=entry.status,
        seen_at=entry.seen_at,
        resolved_at=entry.resolved_at,
        created_at=entry.created_at,
    )


async def _feedback(community_key: str, feedback_id: UUID, db: DbDep) -> FeedbackEntry:
    entry = await db.scalar(
        select(FeedbackEntry)
        .where(FeedbackEntry.id == feedback_id)
        .where(FeedbackEntry.community_key == community_key)
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Feedback not found in this REC")
    return entry


@router.post("/api/feedback", response_model=FeedbackCreateResponse, status_code=201)
async def create_feedback(
    request: Request,
    body: FeedbackCreateRequest,
    user: ConsoleUserDep,
    db: DbDep,
) -> FeedbackCreateResponse:
    """Persist manager feedback with the page diagnostics collected by the UI."""
    # The REC is the caller's claim about which dashboard they were on, so it is
    # checked rather than trusted: without this a manager of one REC could file
    # feedback filed against another.
    decision = await policy.allow_console(user, body.community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")

    screenshot_bytes: bytes | None = None
    screenshot_mime_type: str | None = None
    if body.screenshot:
        try:
            screenshot_bytes = base64.b64decode(body.screenshot.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid screenshot payload") from exc
        screenshot_mime_type = body.screenshot.mime_type

    entry = FeedbackEntry(
        community_key=body.community_key,
        user_id=user.sub,
        rating=body.rating,
        comment=body.comment.strip() or None,
        page_url=body.context.page_url,
        page_title=body.context.page_title,
        page_path=body.context.page_path,
        locale=body.context.locale,
        timezone=body.context.timezone,
        user_agent=body.context.user_agent,
        viewport_width=body.context.viewport_width,
        viewport_height=body.context.viewport_height,
        screen_width=body.context.screen_width,
        screen_height=body.context.screen_height,
        color_scheme=body.context.color_scheme,
        client_timestamp=body.context.client_timestamp,
        client_ip=_client_ip(request),
        extra_context=body.context.extra or None,
        screenshot_mime_type=screenshot_mime_type,
        screenshot_bytes=screenshot_bytes,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return FeedbackCreateResponse(id=entry.id, created_at=entry.created_at)


@router.get(
    "/api/communities/{community_key}/feedback",
    response_model=FeedbackListResponse,
)
async def list_feedback(
    community_key: str,
    user: CommunityReadDep,
    db: DbDep,
    status: FeedbackState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=100),
) -> FeedbackListResponse:
    """List feedback for the caller's REC without exposing stored identity diagnostics."""
    counts_result = await db.execute(
        select(FeedbackEntry.status, func.count(FeedbackEntry.id))
        .where(FeedbackEntry.community_key == community_key)
        .group_by(FeedbackEntry.status)
    )
    counts = {name: count for name, count in counts_result.all()}

    filtered = select(FeedbackEntry).where(FeedbackEntry.community_key == community_key)
    total_query = select(func.count(FeedbackEntry.id)).where(
        FeedbackEntry.community_key == community_key
    )
    if status:
        filtered = filtered.where(FeedbackEntry.status == status)
        total_query = total_query.where(FeedbackEntry.status == status)
    total = int(await db.scalar(total_query) or 0)
    result = await db.execute(
        filtered.order_by(FeedbackEntry.created_at.desc(), FeedbackEntry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return FeedbackListResponse(
        community_key=community_key,
        page=page,
        page_size=page_size,
        total=total,
        counts=FeedbackStatusCounts(
            new=int(counts.get("new", 0)),
            seen=int(counts.get("seen", 0)),
            resolved=int(counts.get("resolved", 0)),
        ),
        items=[_response(entry) for entry in result.scalars().all()],
    )


@router.get("/api/communities/{community_key}/feedback/{feedback_id}/screenshot")
async def feedback_screenshot(
    community_key: str,
    feedback_id: UUID,
    user: CommunityReadDep,
    db: DbDep,
) -> Response:
    """Return an optional screenshot only after the same REC access check as the inbox."""
    entry = await _feedback(community_key, feedback_id, db)
    if entry.screenshot_bytes is None:
        raise HTTPException(status_code=404, detail="Feedback screenshot not found")
    media_type = (
        entry.screenshot_mime_type
        if entry.screenshot_mime_type in _SAFE_IMAGE_TYPES
        else "application/octet-stream"
    )
    return Response(content=entry.screenshot_bytes, media_type=media_type)


@router.patch(
    "/api/communities/{community_key}/feedback/{feedback_id}",
    response_model=FeedbackItemResponse,
)
async def update_feedback_status(
    community_key: str,
    feedback_id: UUID,
    body: FeedbackStatusUpdate,
    user: CommunityReadDep,
    db: DbDep,
) -> FeedbackItemResponse:
    """Advance a feedback item through the manager review workflow."""
    entry = await _feedback(community_key, feedback_id, db)
    if _STATUS_ORDER[body.status] < _STATUS_ORDER[entry.status]:
        raise HTTPException(status_code=409, detail="Feedback status cannot move backward")
    if body.status == entry.status:
        return _response(entry)

    previous = entry.status
    now = datetime.now(UTC)
    if entry.seen_at is None:
        entry.seen_at = now
    if body.status == "resolved":
        entry.resolved_at = now
    entry.status = body.status
    entry.status_updated_by = user.sub
    db.add(
        AuditEvent(
            community_key=community_key,
            actor_id=user.sub,
            action=f"community.feedback.{body.status}",
            resource_type="feedback_entry",
            resource_id=str(entry.id),
            detail={"from": previous, "to": body.status},
        )
    )
    await db.commit()
    await db.refresh(entry)
    return _response(entry)


def _upstream_error(exc: UserFeedbackError) -> HTTPException:
    status = exc.status_code if exc.status_code in {400, 403, 404, 409} else 502
    return HTTPException(status_code=status, detail=exc.detail)


@router.get(
    "/api/communities/{community_key}/user-feedback",
    response_model=FeedbackListResponse,
)
async def list_user_feedback(
    community_key: str,
    user: CommunityReadDep,
    upstream: UserFeedbackDep,
    status: FeedbackState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=100),
) -> FeedbackListResponse:
    """List participant-dashboard feedback owned by celine-webapp for this REC."""
    try:
        return await upstream.list(
            community_key,
            status=status,
            page=page,
            page_size=page_size,
        )
    except UserFeedbackError as exc:
        raise _upstream_error(exc) from exc


@router.get("/api/communities/{community_key}/user-feedback/{feedback_id}/screenshot")
async def user_feedback_screenshot(
    community_key: str,
    feedback_id: UUID,
    user: CommunityReadDep,
    upstream: UserFeedbackDep,
) -> Response:
    try:
        screenshot = await upstream.screenshot(community_key, feedback_id)
    except UserFeedbackError as exc:
        raise _upstream_error(exc) from exc
    return Response(content=screenshot.content, media_type=screenshot.media_type)


@router.patch(
    "/api/communities/{community_key}/user-feedback/{feedback_id}",
    response_model=FeedbackItemResponse,
)
async def update_user_feedback_status(
    community_key: str,
    feedback_id: UUID,
    body: FeedbackStatusUpdate,
    user: CommunityReadDep,
    upstream: UserFeedbackDep,
) -> FeedbackItemResponse:
    try:
        return await upstream.update_status(community_key, feedback_id, body.status)
    except UserFeedbackError as exc:
        raise _upstream_error(exc) from exc
