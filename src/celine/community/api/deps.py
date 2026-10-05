"""Authentication, authorization, and downstream client dependencies."""

import logging
from typing import Annotated

import jwt as pyjwt
from celine.sdk.audit import audit_denied
from celine.sdk.auth import PLATFORM_ADMIN_ROLE, JwtUser, OidcClientCredentialsProvider
from celine.sdk.auth.jwt import Organization
from celine.sdk.dt import DTClient
from celine.sdk.nudging import NudgingAdminClient
from celine.sdk.onboarding import OnboardingAdminClient
from celine.sdk.rec_registry import RecRegistryAdminClient
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from celine.community.db import get_db
from celine.community.security.policy import policy
from celine.community.services.recs import has_console_access
from celine.community.services.user_feedback import RoiFeedbackClient, UserFeedbackClient
from celine.community.settings import settings

logger = logging.getLogger(__name__)

#: The REC the development fixtures belong to: the generic sample REC, which the
#: seeded realm and registry both carry. It must be an existing Keycloak
#: organization alias, which is also the REC registry's community key — the two
#: are one string on this platform. Never a real community's key: this is a
#: public repository.
DEV_COMMUNITY_KEY = "example_rec"

#: The action a refusal at the token check is recorded under on `celine.audit`.
#: Every other refusal is recorded under the policy action it was refused.
AUTHENTICATE = "authenticate"

NO_REC_DETAIL = (
    "No REC grants you access. Ask a REC administrator to add you to its "
    "Keycloak organization as a manager."
)

dt_token_provider = OidcClientCredentialsProvider(
    base_url=settings.oidc.base_url,
    client_id=settings.oidc.client_id or "",
    client_secret=settings.oidc.client_secret or "",
    scope=settings.digital_twin_scope,
    timeout=settings.downstream_timeout_seconds,
    verify_ssl=settings.oidc.verify_ssl,
)
registry_token_provider = OidcClientCredentialsProvider(
    base_url=settings.oidc.base_url,
    client_id=settings.oidc.client_id or "",
    client_secret=settings.oidc.client_secret or "",
    scope=settings.rec_registry_scope,
    timeout=settings.downstream_timeout_seconds,
    verify_ssl=settings.oidc.verify_ssl,
)
#: Only for attaching and detaching a meter (ADR-0003). Its own provider, so the
#: default registry token never carries a write scope: the Digital Twin forwards it.
registry_assets_write_token_provider = OidcClientCredentialsProvider(
    base_url=settings.oidc.base_url,
    client_id=settings.oidc.client_id or "",
    client_secret=settings.oidc.client_secret or "",
    scope=settings.rec_registry_assets_write_scope,
    timeout=settings.downstream_timeout_seconds,
    verify_ssl=settings.oidc.verify_ssl,
)
#: Only for correcting a member's role and area (ADR-0003), for the same reason.
registry_profile_write_token_provider = OidcClientCredentialsProvider(
    base_url=settings.oidc.base_url,
    client_id=settings.oidc.client_id or "",
    client_secret=settings.oidc.client_secret or "",
    scope=settings.rec_registry_profile_write_scope,
    timeout=settings.downstream_timeout_seconds,
    verify_ssl=settings.oidc.verify_ssl,
)
nudging_token_provider = OidcClientCredentialsProvider(
    base_url=settings.oidc.base_url,
    client_id=settings.oidc.client_id or "",
    client_secret=settings.oidc.client_secret or "",
    scope=settings.nudging_scope,
    timeout=settings.downstream_timeout_seconds,
    verify_ssl=settings.oidc.verify_ssl,
)
#: Onboarding sends member emails for this BFF. Its own provider, because the scope
#: is its own: a token for the registry must not be able to send an invitation.
onboarding_token_provider = OidcClientCredentialsProvider(
    base_url=settings.oidc.base_url,
    client_id=settings.oidc.client_id or "",
    client_secret=settings.oidc.client_secret or "",
    scope=settings.onboarding_scope,
    timeout=settings.downstream_timeout_seconds,
    verify_ssl=settings.oidc.verify_ssl,
)


def extract_token(request: Request) -> str | None:
    token = request.headers.get(settings.jwt_header_name)
    if token:
        return token
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def get_user_feedback_client(request: Request) -> UserFeedbackClient:
    if not settings.webapp_api_url:
        raise HTTPException(status_code=503, detail="Participant feedback API not configured")
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    return UserFeedbackClient(
        settings.webapp_api_url,
        token,
        timeout=settings.downstream_timeout_seconds,
    )


def get_roi_feedback_client(request: Request) -> RoiFeedbackClient:
    if not settings.roi_api_url:
        raise HTTPException(status_code=503, detail="ROI feedback API not configured")
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    return RoiFeedbackClient(
        settings.roi_api_url,
        token,
        timeout=settings.downstream_timeout_seconds,
    )


_DEV_SCOPE = "community.read community.devices.read community.nudging.read community.alerts.write"


def _development_user() -> JwtUser:
    """A signed-in caller, without a Keycloak round trip.

    Two fixtures, because the policy now has two branches that must both be
    exercisable locally: `DEV_USER_PROFILE=manager` is an organization-scoped
    manager of one REC, `DEV_USER_PROFILE=admin` holds the `platform-admin` realm
    role, belongs to no organization and sees every REC the registry lists.

    The claim shape is the one a real KC 26.4 token carries, measured against the
    celine realm: per-organization `type` **flattened** rather than nested under
    `attributes`, group names carrying a leading slash, and realm roles in
    `realm_access.roles` rather than in `groups`. A fixture that models
    the shape wrongly is a fixture that passes while production denies.
    """
    admin = settings.dev_user_profile == "admin"

    claims: dict = {
        "sub": settings.dev_user_sub,
        "email": settings.dev_user_email,
        "name": settings.dev_user_name,
        "preferred_username": "community-manager-dev",
        "locale": "it",
        "realm_access": {"roles": [PLATFORM_ADMIN_ROLE] if admin else []},
        "scope": _DEV_SCOPE,
        "organization": {}
        if admin
        else {DEV_COMMUNITY_KEY: {"type": ["rec"], "groups": ["/managers"]}},
    }
    return JwtUser(
        sub=settings.dev_user_sub,
        email=settings.dev_user_email,
        name=settings.dev_user_name,
        preferred_username="community-manager-dev",
        organizations=[
            Organization._from_claim(alias, data) for alias, data in claims["organization"].items()
        ],
        claims=claims,
    )


def _token_failure(exc: Exception) -> str:
    if isinstance(exc, pyjwt.ExpiredSignatureError):
        return "token_expired"
    if isinstance(exc, pyjwt.InvalidTokenError):
        return "token_invalid"
    return "token_unverified"


def get_user_from_request(request: Request) -> JwtUser:
    if settings.dev_auth_enabled and settings.is_dev:
        return _development_user()

    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        return JwtUser.from_token(token, oidc=settings.oidc)
    except Exception as exc:
        # A presented token that does not verify: recorded with no caller, because
        # the claims of an unverified token are not trusted.
        audit_denied(AUTHENTICATE, reason=_token_failure(exc), request=request)
        if isinstance(exc, pyjwt.ExpiredSignatureError):
            raise HTTPException(status_code=401, detail="Token has expired") from exc
        if isinstance(exc, pyjwt.InvalidTokenError):
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
        raise HTTPException(status_code=401, detail="Authentication failed") from exc


def refuse(
    action: str,
    user: JwtUser,
    request: Request,
    *,
    community_key: str | None = None,
    reason: str | None = None,
    detail: str | None = None,
) -> HTTPException:
    """The ``403`` for a refused *action*, recorded on ``celine.audit`` first.

    The record names the caller by ``sub`` and client id and the REC by its key;
    never an email or a name. ``reason`` is the policy's short reason.
    """
    audit_denied(
        action,
        caller=user,
        resource=community_key,
        reason=reason or "denied",
        request=request,
    )
    return HTTPException(status_code=403, detail=detail or reason or "access denied")


async def require_console_access(
    request: Request,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    """Signed in, and a manager of *something*.

    Deliberately does not name a REC. It guards `/api/ping` and `/api/me`, which
    are about the caller rather than about one community; which REC is being
    opened is the path parameter every other dependency already receives.
    """
    if not await has_console_access(user):
        raise refuse("console.read", user, request, reason="no_rec", detail=NO_REC_DETAIL)
    return user


async def _require(action: str, request: Request, community_key: str, user: JwtUser) -> JwtUser:
    decision = await policy.allow(user, action, community_key)
    if not decision.allowed:
        raise refuse(action, user, request, community_key=community_key, reason=decision.reason)
    return user


async def require_community_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("community.read", request, community_key, user)


async def require_objectives_write(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("objectives.write", request, community_key, user)


async def require_devices_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("devices.read", request, community_key, user)


async def require_flexibility_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("flexibility.read", request, community_key, user)


async def require_gamification_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("gamification.read", request, community_key, user)


async def require_nudging_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("nudging.read", request, community_key, user)


async def require_alerts_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("alerts.read", request, community_key, user)


async def require_alerts_write(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("alerts.write", request, community_key, user)


async def require_members_read(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("members.read", request, community_key, user)


async def require_members_invite(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("members.invite", request, community_key, user)


async def require_members_meter(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("members.meter", request, community_key, user)


async def require_members_edit(
    request: Request,
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    return await _require("members.edit", request, community_key, user)


def get_dt_client() -> DTClient:
    if not settings.digital_twin_api_url:
        raise HTTPException(status_code=503, detail="Digital Twin API not configured")
    return DTClient(
        base_url=settings.digital_twin_api_url,
        token_provider=dt_token_provider,
        timeout=settings.downstream_timeout_seconds,
    )


def get_registry_client() -> RecRegistryAdminClient:
    if not settings.rec_registry_url:
        raise HTTPException(status_code=503, detail="REC Registry API not configured")
    return RecRegistryAdminClient(
        base_url=settings.rec_registry_url,
        token_provider=registry_token_provider,
        timeout=settings.downstream_timeout_seconds,
        verify_ssl=settings.oidc.verify_ssl,
    )


def get_registry_assets_writer() -> RecRegistryAdminClient:
    """The registry client for a meter attach or detach, and for nothing else.

    It carries `rec-registry.assets.write`; every other registry call keeps
    `get_registry_client` and its default `rec-registry.read` token.
    """
    if not (settings.rec_registry_url and settings.rec_registry_assets_write_scope):
        raise HTTPException(status_code=503, detail={"code": "meter_writes_not_configured"})
    return RecRegistryAdminClient(
        base_url=settings.rec_registry_url,
        token_provider=registry_assets_write_token_provider,
        timeout=settings.downstream_timeout_seconds,
        verify_ssl=settings.oidc.verify_ssl,
    )


def get_registry_profile_writer() -> RecRegistryAdminClient:
    """The registry client for a role or area correction, and for nothing else.

    It carries `rec-registry.members.profile.write`; every other registry call
    keeps `get_registry_client` and its default `rec-registry.read` token.
    """
    if not (settings.rec_registry_url and settings.rec_registry_profile_write_scope):
        raise HTTPException(status_code=503, detail={"code": "profile_writes_not_configured"})
    return RecRegistryAdminClient(
        base_url=settings.rec_registry_url,
        token_provider=registry_profile_write_token_provider,
        timeout=settings.downstream_timeout_seconds,
        verify_ssl=settings.oidc.verify_ssl,
    )


def get_nudging_client() -> NudgingAdminClient:
    if not settings.nudging_api_url:
        raise HTTPException(status_code=503, detail="Nudging API not configured")
    return NudgingAdminClient(
        base_url=settings.nudging_api_url,
        token_provider=nudging_token_provider,
        timeout=settings.downstream_timeout_seconds,
        verify_ssl=settings.oidc.verify_ssl,
    )


def get_onboarding_client() -> OnboardingAdminClient:
    if not settings.onboarding_url:
        raise HTTPException(status_code=503, detail={"code": "onboarding_not_configured"})
    return OnboardingAdminClient(
        base_url=settings.onboarding_url,
        token_provider=onboarding_token_provider,
        timeout=settings.downstream_timeout_seconds,
        verify_ssl=settings.oidc.verify_ssl,
    )


UserDep = Annotated[JwtUser, Depends(get_user_from_request)]
ConsoleUserDep = Annotated[JwtUser, Depends(require_console_access)]
CommunityReadDep = Annotated[JwtUser, Depends(require_community_read)]
ObjectivesWriteDep = Annotated[JwtUser, Depends(require_objectives_write)]
DevicesReadDep = Annotated[JwtUser, Depends(require_devices_read)]
FlexibilityReadDep = Annotated[JwtUser, Depends(require_flexibility_read)]
GamificationReadDep = Annotated[JwtUser, Depends(require_gamification_read)]
NudgingReadDep = Annotated[JwtUser, Depends(require_nudging_read)]
AlertsReadDep = Annotated[JwtUser, Depends(require_alerts_read)]
AlertsWriteDep = Annotated[JwtUser, Depends(require_alerts_write)]
MembersReadDep = Annotated[JwtUser, Depends(require_members_read)]
MembersInviteDep = Annotated[JwtUser, Depends(require_members_invite)]
MembersMeterDep = Annotated[JwtUser, Depends(require_members_meter)]
MembersEditDep = Annotated[JwtUser, Depends(require_members_edit)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
DTDep = Annotated[DTClient, Depends(get_dt_client)]
RegistryDep = Annotated[RecRegistryAdminClient, Depends(get_registry_client)]
RegistryAssetsWriterDep = Annotated[RecRegistryAdminClient, Depends(get_registry_assets_writer)]
RegistryProfileWriterDep = Annotated[RecRegistryAdminClient, Depends(get_registry_profile_writer)]
NudgingDep = Annotated[NudgingAdminClient, Depends(get_nudging_client)]
OnboardingDep = Annotated[OnboardingAdminClient, Depends(get_onboarding_client)]
UserFeedbackDep = Annotated[UserFeedbackClient, Depends(get_user_feedback_client)]
RoiFeedbackDep = Annotated[RoiFeedbackClient, Depends(get_roi_feedback_client)]
