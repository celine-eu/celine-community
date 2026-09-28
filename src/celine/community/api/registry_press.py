"""What a manager's press on registry data shares, whichever write it is (ADR-0003).

The meter attach and detach (`member_meter`) and the profile edit
(`member_profile`) read the registry with the default `rec-registry.read` token,
write with a token asked for one optional scope, and record one audit row per
press. This module is the part they have in common: the read, the outcome, and
the audit row. What each write refuses, and how, stays with its route.

Nothing here logs a registry body: the refusal sentence may carry an asset key
(and so a sensor id) or a member's data. Log lines name the community key, the
member key, the status and the code.
"""

import logging
from dataclasses import dataclass
from typing import Any

from celine.sdk.auth import JwtUser
from celine.sdk.openapi.rec_registry.errors import UnexpectedStatus
from celine.sdk.rec_registry import RecRegistryApiError

from celine.community.db.models import AuditEvent

logger = logging.getLogger(__name__)


class Refused(Exception):
    """A press that ends in a refusal: the status and code the manager is told."""

    def __init__(self, status: int, code: str, upstream: int | None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.upstream = upstream


@dataclass(frozen=True)
class Outcome:
    status: int
    code: str
    #: What the registry answered to the last call of the press. None when it did not.
    upstream: int | None


def text(value: Any) -> str | None:
    """A generated enum or a plain string, as the string."""
    if value is None:
        return None
    return str(getattr(value, "value", value))


def is_active(member: Any) -> bool:
    """Whether the registry member's status is `active` (ADR-0004).

    An attach and a role or area edit are for active members only; a detach is
    open for every member, so a suspended member's meter can be freed.
    """
    return (text(getattr(member, "status", None)) or "").strip().casefold() == "active"


def not_found(content: bytes | None, fallback: str) -> str:
    code, _ = RecRegistryApiError.refusal_of(content)
    return code if code in ("member_not_found", "community_not_found") else fallback


def log_upstream(what, community_key, member_key, status, code) -> None:
    logger.warning(
        "REC Registry %s refused community=%s member=%s status=%s code=%s",
        what,
        community_key,
        member_key,
        status,
        code,
    )


async def read(call, what: str, community_key: str, member_key: str, *, missing: str):
    """One registry read with the default token; a refusal is raised as `Refused`.

    A `404` is *missing* unless the registry's code says whether the member or
    the community is the one missing.
    """
    try:
        response = await call
    except UnexpectedStatus as exc:
        # Not `str(exc)`: it carries the registry's body.
        if exc.status_code == 404:
            raise Refused(404, not_found(exc.content, missing), 404) from exc
        log_upstream(what, community_key, member_key, exc.status_code, None)
        if exc.status_code in (401, 403):
            raise Refused(502, "registry_refused", exc.status_code) from exc
        raise Refused(502, "registry_unavailable", exc.status_code) from exc
    except Exception as exc:
        logger.warning(
            "REC Registry %s unavailable community=%s member=%s error=%s",
            what,
            community_key,
            member_key,
            type(exc).__name__,
        )
        raise Refused(502, "registry_unavailable", None) from exc
    parsed = getattr(response, "parsed", None)
    if parsed is None or response.status_code != 200:
        log_upstream(what, community_key, member_key, response.status_code, None)
        raise Refused(502, "registry_unavailable", response.status_code)
    return parsed


async def audit(
    db,
    community_key: str,
    member_key: str,
    actor: JwtUser,
    action: str,
    detail: dict[str, Any],
) -> None:
    """One row per press, resource `registry_member`, naming the member by key.

    *detail* is the caller's, and must hold no personal data: no name, email,
    address or sensor id. A failed commit is logged and does not undo the press,
    which the registry has already answered.
    """
    try:
        db.add(
            AuditEvent(
                community_key=community_key,
                actor_id=actor.sub,
                action=action,
                resource_type="registry_member",
                resource_id=member_key,
                detail=detail,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.error(
            "Audit row NOT written for a registry press: community=%s member=%s action=%s "
            "actor=%s detail=%s",
            community_key,
            member_key,
            action,
            actor.sub,
            detail,
        )
        try:
            await db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rollback after the failed audit commit failed: %s", type(exc).__name__)
