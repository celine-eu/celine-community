"""A manager sends a member an invitation, or a password reset.

    dashboard ─▶ these routes ─▶ onboarding ─▶ provisioning ─▶ Keycloak

This BFF never calls the provisioning service (requester, 2026-09-14, A3). It
calls onboarding with two tokens: its own, carrying `onboarding.members.invite`,
and the manager's, in `X-Acting-User-Token`. Onboarding verifies both and
authorises the pair. The policy here has already checked the manager against this
REC, and onboarding checks them again from the forwarded token.

**Two routes, each an explicit intent** (A2). Onboarding refuses a mismatch with
`409 has_password` / `no_password`, before any email and without starting the
cooldown. Nothing here turns one intent into the other.

**Codes, never messages** (A6). The answer is `{"detail": {"code": …}}`, with
`retryAfterSeconds` on a `cooldown`. Onboarding's English message is neither
forwarded nor parsed.

**Every press that reaches onboarding writes one audit row**, refusals included.
The row names the member key only. It is written after the call: an email that
has gone cannot be taken back, so a failed commit is logged and the manager is
still told what happened.
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Literal

import httpx
from celine.sdk.auth import JwtUser
from celine.sdk.onboarding import OnboardingApiError
from fastapi import APIRouter, HTTPException, Path, Request

from celine.community.api.deps import DbDep, MembersInviteDep, OnboardingDep, extract_token
from celine.community.api.schemas import MemberEmailSent
from celine.community.db.models import AuditEvent
from celine.community.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communities/{community_key}/members/{member_key}", tags=["members"])

Intent = Literal["invitation", "password_reset"]

#: The audit action for each intent.
AUDIT_ACTIONS: dict[str, str] = {
    "invitation": "community.member.invitation",
    "password_reset": "community.member.password_reset",
}

#: Statuses onboarding answers with a code of its own or of the provisioning
#: service's, passed through unchanged.
_PASSED_THROUGH = frozenset({404, 409, 429, 502, 503})

MemberKey = Annotated[str, Path(min_length=1, max_length=100)]


@dataclass(frozen=True)
class _Outcome:
    status: int
    code: str
    kind: str
    #: What onboarding answered, for the audit row. None when it did not answer.
    upstream: int | None
    lifespan_seconds: int | None = None
    retry_after_seconds: int | None = None


def _text(value: object) -> str:
    """A generated enum never equals its string; compare and store the string."""
    return str(getattr(value, "value", value))


def _refused(exc: OnboardingApiError, intent: str, community_key: str, member_key: str) -> _Outcome:
    status = exc.status_code or 502
    code = exc.code or f"http_{status}"
    if status in (401, 403):
        # A missing grant or a forwarded token onboarding cannot verify is a
        # deployment fault. A 403 here would tell the manager *they* were refused.
        logger.error(
            "Onboarding refused this BFF community=%s member=%s status=%s code=%s",
            community_key,
            member_key,
            status,
            code,
        )
        return _Outcome(502, "onboarding_refused", intent, upstream=status)
    return _Outcome(
        status if status in _PASSED_THROUGH else 502,
        code,
        intent,
        upstream=status,
        retry_after_seconds=exc.retry_after_seconds if code == "cooldown" else None,
    )


async def _audit(
    db,
    community_key: str,
    member_key: str,
    actor: JwtUser,
    intent: str,
    outcome: _Outcome,
) -> None:
    detail = {
        "code": outcome.code,
        "kind": outcome.kind,
        "lifespan_seconds": outcome.lifespan_seconds,
        "status": outcome.upstream,
    }
    try:
        db.add(
            AuditEvent(
                community_key=community_key,
                actor_id=actor.sub,
                action=AUDIT_ACTIONS[intent],
                resource_type="registry_member",
                resource_id=member_key,
                detail=detail,
            )
        )
        await db.commit()
    except Exception:
        logger.exception(
            "Audit row NOT written for a member email: community=%s member=%s action=%s "
            "actor=%s detail=%s",
            community_key,
            member_key,
            AUDIT_ACTIONS[intent],
            actor.sub,
            detail,
        )
        try:
            await db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rollback after the failed audit commit failed: %s", type(exc).__name__)


async def _email_member(
    intent: Intent,
    community_key: str,
    member_key: str,
    user: JwtUser,
    onboarding,
    db,
    request: Request,
) -> MemberEmailSent:
    acting_token = extract_token(request)
    if not acting_token:
        # Only development authentication gets here: in production the caller was
        # authenticated from this very token. Not a 401, which the dashboard reads
        # as "sign in again" and would loop on.
        raise HTTPException(status_code=503, detail={"code": "acting_token_unavailable"})

    send = (
        onboarding.send_member_invitation
        if intent == "invitation"
        else onboarding.send_member_password_reset
    )
    try:
        sent = await send(community_key, member_key, acting_token=acting_token)
        outcome = _Outcome(
            200,
            _text(sent.code),
            _text(sent.kind),
            upstream=200,
            lifespan_seconds=sent.lifespanSeconds,
        )
    except OnboardingApiError as exc:
        outcome = _refused(exc, intent, community_key, member_key)
    except httpx.HTTPStatusError as exc:
        # Only the token provider raises this: the onboarding client turns every
        # answer into `OnboardingApiError`. Keycloak refusing the token request is a
        # deployment fault, most often `invalid_scope` on a realm that has not been
        # synced with `onboarding.members.invite`. It is not an outage.
        logger.error(
            "Keycloak refused this BFF's token for onboarding community=%s member=%s "
            "status=%s (is %s assigned to the client on this realm?)",
            community_key,
            member_key,
            exc.response.status_code,
            settings.onboarding_scope,
        )
        outcome = _Outcome(502, "onboarding_refused", intent, upstream=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Onboarding unavailable community=%s member=%s error=%s",
            community_key,
            member_key,
            type(exc).__name__,
        )
        outcome = _Outcome(503, "onboarding_unavailable", intent, upstream=None)

    await _audit(db, community_key, member_key, user, intent, outcome)
    logger.info(
        "Member email community=%s member=%s intent=%s code=%s status=%s actor=%s",
        community_key,
        member_key,
        intent,
        outcome.code,
        outcome.status,
        user.sub,
    )

    if outcome.status == 200 and outcome.lifespan_seconds is not None:
        return MemberEmailSent(
            code=outcome.code,
            kind=outcome.kind,
            lifespan_seconds=outcome.lifespan_seconds,
        )
    detail: dict[str, object] = {"code": outcome.code}
    headers = None
    if outcome.retry_after_seconds is not None:
        detail["retryAfterSeconds"] = outcome.retry_after_seconds
        headers = {"Retry-After": str(outcome.retry_after_seconds)}
    raise HTTPException(status_code=outcome.status, detail=detail, headers=headers)


@router.post("/invitation", response_model=MemberEmailSent)
async def send_member_invitation(
    community_key: str,
    member_key: MemberKey,
    user: MembersInviteDep,
    onboarding: OnboardingDep,
    db: DbDep,
    request: Request,
) -> MemberEmailSent:
    """Email the member an invitation to set their first password.

    Refused `409 has_password` when the account already has one.
    """
    return await _email_member(
        "invitation", community_key, member_key, user, onboarding, db, request
    )


@router.post("/password-reset", response_model=MemberEmailSent)
async def send_member_password_reset(
    community_key: str,
    member_key: MemberKey,
    user: MembersInviteDep,
    onboarding: OnboardingDep,
    db: DbDep,
    request: Request,
) -> MemberEmailSent:
    """Email the member a link to reset their password.

    Refused `409 no_password` when the account has none; never turned into an
    invitation.
    """
    return await _email_member(
        "password_reset", community_key, member_key, user, onboarding, db, request
    )
