"""A REC admin releases a member from their community.

    dashboard ─▶ this route ─▶ onboarding ─▶ connector, identity registry,
                                             provisioning (Keycloak), REC registry

Releasing ends a person's membership of this REC so that they can join another:
their data sharing is withdrawn, their dataspace credential revoked, their login
removed from the REC's organization, and the registry member set `inactive`.
Nothing is deleted; records stay for the retention period.

**Onboarding does the work; this BFF delegates**, exactly as it does for a member
email (`member_emails.py`). It sends two tokens: its own, asked for the optional
scope `onboarding.members.release` alone, and the admin's, in
`X-Acting-User-Token`. The policy here has already checked that the caller is an
**admin** of this REC (`members.release`, managers refused), and onboarding checks
them again from the forwarded token.

**A release that ran is a `200`, also when part of it failed.** Onboarding answers
`state: released | partial` with four steps in a fixed order. The steps' codes are
passed through; their English `detail` is logged and never returned. Releasing
again is the retry: onboarding's release is idempotent.

**Refusals are codes, never messages**, as for the emails. A `401`/`403` from
onboarding is this deployment's fault (a missing grant or scope), so it is
answered `502 onboarding_refused` rather than telling the admin *they* were
refused.

**Every press that reaches onboarding writes one audit row**, refusals included,
naming the member key and each step's code only.
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated

import httpx
from celine.sdk.auth import JwtUser
from celine.sdk.onboarding import OnboardingApiError
from fastapi import APIRouter, HTTPException, Path, Request

from celine.community.api.deps import DbDep, MembersReleaseDep, OnboardingReleaseDep, extract_token
from celine.community.api.schemas import MemberReleased, MemberReleaseStep
from celine.community.db.models import AuditEvent
from celine.community.services.onboarding_release import ReleaseResult
from celine.community.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/communities/{community_key}/members/{member_key}", tags=["members"])

AUDIT_ACTION = "community.member.release"

#: Statuses onboarding answers with a code of its own, passed through unchanged:
#: 404 `member_not_found` / `community_not_served`, 409 `community_ambiguous`,
#: 502 `registry_unavailable`, 503 `admin_not_configured`.
_PASSED_THROUGH = frozenset({404, 409, 502, 503})

MemberKey = Annotated[str, Path(min_length=1, max_length=100)]


@dataclass(frozen=True)
class _Outcome:
    status: int
    code: str
    #: What onboarding answered, for the audit row. None when it did not answer.
    upstream: int | None
    result: ReleaseResult | None = None
    steps: dict[str, str] = field(default_factory=dict)


def _refused(exc: OnboardingApiError, community_key: str, member_key: str) -> _Outcome:
    status = exc.status_code or 502
    code = exc.code or f"http_{status}"
    if status in (401, 403):
        # A missing grant or a forwarded token onboarding cannot verify is a
        # deployment fault: this policy has already let the admin through.
        logger.error(
            "Onboarding refused this BFF's member release community=%s member=%s status=%s code=%s",
            community_key,
            member_key,
            status,
            code,
        )
        return _Outcome(502, "onboarding_refused", upstream=status)
    if status == 200:
        logger.error(
            "Onboarding answered a member release with nothing readable community=%s member=%s",
            community_key,
            member_key,
        )
        return _Outcome(502, code, upstream=status)
    return _Outcome(status if status in _PASSED_THROUGH else 502, code, upstream=status)


async def _audit(
    db, community_key: str, member_key: str, actor: JwtUser, outcome: _Outcome
) -> None:
    detail = {
        "code": outcome.code,
        "status": outcome.upstream,
        "source": outcome.result.source if outcome.result else None,
        "steps": outcome.steps,
    }
    try:
        db.add(
            AuditEvent(
                community_key=community_key,
                actor_id=actor.sub,
                action=AUDIT_ACTION,
                resource_type="registry_member",
                resource_id=member_key,
                detail=detail,
            )
        )
        await db.commit()
    except Exception:
        # The release has happened (or partly): it cannot be taken back, so the
        # admin is still told the outcome and the missing row is logged in full.
        logger.exception(
            "Audit row NOT written for a member release: community=%s member=%s action=%s "
            "actor=%s detail=%s",
            community_key,
            member_key,
            AUDIT_ACTION,
            actor.sub,
            detail,
        )
        try:
            await db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rollback after the failed audit commit failed: %s", type(exc).__name__)


@router.post("/release", response_model=MemberReleased)
async def release_member(
    community_key: str,
    member_key: MemberKey,
    user: MembersReleaseDep,
    onboarding: OnboardingReleaseDep,
    db: DbDep,
    request: Request,
) -> MemberReleased:
    """Release the member: withdraw data sharing, revoke the dataspace credential,
    remove the login from this REC, set the member inactive.

    `200` with `state` `released` or `partial` and the four steps. Releasing again
    is the retry. REC admins and platform admins only.
    """
    acting_token = extract_token(request)
    if not acting_token:
        # Only development authentication gets here. Not a 401, which the dashboard
        # reads as "sign in again" and would loop on.
        raise HTTPException(status_code=503, detail={"code": "acting_token_unavailable"})

    try:
        result = await onboarding.release_member(
            community_key, member_key, acting_token=acting_token
        )
        outcome = _Outcome(
            200,
            result.state,
            upstream=200,
            result=result,
            steps={step.step: step.code for step in result.steps},
        )
    except OnboardingApiError as exc:
        outcome = _refused(exc, community_key, member_key)
    except httpx.HTTPStatusError as exc:
        # Only the token provider raises this. Keycloak refusing the token request is
        # a deployment fault, most often `invalid_scope` on a realm where the optional
        # scope is not assigned to this client.
        logger.error(
            "Keycloak refused this BFF's token for a member release community=%s member=%s "
            "status=%s (is %s assigned to the client on this realm?)",
            community_key,
            member_key,
            exc.response.status_code,
            settings.onboarding_release_scope,
        )
        outcome = _Outcome(502, "onboarding_refused", upstream=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Onboarding unavailable for a member release community=%s member=%s error=%s",
            community_key,
            member_key,
            type(exc).__name__,
        )
        outcome = _Outcome(503, "onboarding_unavailable", upstream=None)

    await _audit(db, community_key, member_key, user, outcome)
    logger.info(
        "Member release community=%s member=%s code=%s status=%s actor=%s",
        community_key,
        member_key,
        outcome.code,
        outcome.status,
        user.sub,
    )
    if outcome.result is not None:
        for step in outcome.result.steps:
            if step.status in ("failed", "blocked"):
                logger.warning(
                    "Member release step community=%s member=%s step=%s status=%s code=%s "
                    "detail=%s",
                    community_key,
                    member_key,
                    step.step,
                    step.status,
                    step.code,
                    step.detail,
                )
        return MemberReleased(
            member_key=member_key,
            state=outcome.result.state,
            source=outcome.result.source,
            steps=[
                MemberReleaseStep(step=step.step, status=step.status, code=step.code)
                for step in outcome.result.steps
            ],
        )
    raise HTTPException(status_code=outcome.status, detail={"code": outcome.code})
