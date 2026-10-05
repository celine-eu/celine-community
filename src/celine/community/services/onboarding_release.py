"""Onboarding's delegated member release, as this BFF calls it.

    POST {onboarding}/api/admin/communities/{community}/members/{member_key}/release

The installed celine-sdk wraps onboarding's invitation and password reset but not
the release, so this is the one call made here directly. It sends what the SDK's
`OnboardingAdminClient` sends: the BFF's own token in `Authorization` (asked for
`onboarding.members.release` only) and the REC admin's token in
`X-Acting-User-Token`. No body. Every non-`200` is raised as the SDK's
`OnboardingApiError`, with onboarding's `{"detail": {"code", "message"}}` read into
`code` and `detail`, so the route maps it exactly as it maps a member email.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from celine.sdk.auth import TokenProvider
from celine.sdk.onboarding import OnboardingApiError
from celine.sdk.onboarding.client import ACTING_USER_HEADER


@dataclass(frozen=True)
class ReleaseStep:
    step: str
    status: str
    code: str
    #: An English sentence for logs. Never forwarded to the dashboard.
    detail: str | None


@dataclass(frozen=True)
class ReleaseResult:
    community: str
    member_key: str
    state: str
    source: str | None
    steps: tuple[ReleaseStep, ...]


def _body(content: bytes) -> Any:
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_release(body: Any) -> ReleaseResult | None:
    """The `200` as the contract shapes it, or `None` when it is not that shape.

    Steps are kept in the order onboarding sent them; a step or a code this BFF
    does not know is kept as it is, because new codes may be added.
    """
    if not isinstance(body, dict):
        return None
    state = _text(body.get("state"))
    raw_steps = body.get("steps")
    if state is None or not isinstance(raw_steps, list):
        return None
    steps: list[ReleaseStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            return None
        step, status, code = (_text(raw.get(name)) for name in ("step", "status", "code"))
        if step is None or status is None or code is None:
            return None
        steps.append(ReleaseStep(step, status, code, _text(raw.get("detail"))))
    return ReleaseResult(
        community=_text(body.get("community")) or "",
        member_key=_text(body.get("memberKey")) or "",
        state=state,
        source=_text(body.get("source")),
        steps=tuple(steps),
    )


class OnboardingReleaseClient:
    """One call: release a registry member of one community.

    `community` is the REC registry's community key, which on this platform is also
    the admin's organization alias. Nothing is retried: releasing again is the
    admin's decision, and onboarding's release is idempotent when they make it.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token_provider: TokenProvider,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._timeout = httpx.Timeout(timeout)
        self._verify_ssl = verify_ssl

    def url(self, community: str, member_key: str) -> str:
        return (
            f"{self._base_url}/api/admin/communities/{quote(community, safe='')}"
            f"/members/{quote(member_key, safe='')}/release"
        )

    async def release_member(
        self, community: str, member_key: str, *, acting_token: str
    ) -> ReleaseResult:
        if not acting_token or not acting_token.strip():
            raise ValueError("acting_token is required: a release follows a person's press")
        service_token = (await self._token_provider.get_token()).access_token
        async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify_ssl) as client:
            response = await client.post(
                self.url(community, member_key),
                headers={
                    "Authorization": f"Bearer {service_token}",
                    ACTING_USER_HEADER: acting_token,
                },
            )
        body = _body(response.content)
        if response.status_code != 200:
            raw = body.get("detail") if isinstance(body, dict) else None
            code = raw.get("code") if isinstance(raw, dict) else None
            message = raw.get("message") if isinstance(raw, dict) else raw
            raise OnboardingApiError(
                f"release_member failed: onboarding answered {response.status_code}"
                + (f" {code}" if code else ""),
                status_code=response.status_code,
                detail=str(message) if message is not None else None,
                body=response.content,
                code=str(code) if code is not None else None,
            )
        result = parse_release(body)
        if result is None:
            raise OnboardingApiError(
                "release_member failed: onboarding answered 200 with nothing readable",
                status_code=200,
                body=response.content,
                code="onboarding_unreadable",
            )
        return result
