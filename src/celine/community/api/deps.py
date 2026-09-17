"""Authentication, authorization, and downstream client dependencies."""

import logging
from typing import Annotated

import jwt as pyjwt
from celine.sdk.auth import JwtUser, OidcClientCredentialsProvider
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
from celine.community.settings import settings

logger = logging.getLogger(__name__)

#: The REC the development fixtures belong to: the generic sample REC, which the
#: seeded realm and registry both carry. It must be an existing Keycloak
#: organization alias, which is also the REC registry's community key — the two
#: are one string on this platform. Never a real community's key: this is a
#: public repository.
DEV_COMMUNITY_KEY = "example_rec"

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


_DEV_SCOPE = "community.read community.devices.read community.nudging.read community.alerts.write"


def _development_user() -> JwtUser:
    """A signed-in caller, without a Keycloak round trip.

    Two fixtures, because the policy now has two branches that must both be
    exercisable locally: `DEV_USER_PROFILE=manager` is an organization-scoped
    manager of one REC, `DEV_USER_PROFILE=admin` is a realm admin who belongs to
    no organization and sees every REC the registry lists.

    The claim shape is the one a real KC 26.4 token carries, measured against the
    celine realm: per-organization `type` **flattened** rather than nested under
    `attributes`, and group names carrying a leading slash. A fixture that models
    the shape wrongly is a fixture that passes while production denies.
    """
    admin = settings.dev_user_profile == "admin"

    claims: dict = {
        "sub": settings.dev_user_sub,
        "email": settings.dev_user_email,
        "name": settings.dev_user_name,
        "preferred_username": "community-manager-dev",
        "locale": "it",
        "groups": ["/admins"] if admin else [],
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


def get_user_from_request(request: Request) -> JwtUser:
    if settings.dev_auth_enabled and settings.environment != "production":
        return _development_user()

    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        return JwtUser.from_token(token, oidc=settings.oidc)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token has expired") from exc
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Authentication failed") from exc


async def require_console_access(
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    """Signed in, and a manager of *something*.

    Deliberately does not name a REC. It guards `/api/ping` and `/api/me`, which
    are about the caller rather than about one community; which REC is being
    opened is the path parameter every other dependency already receives.
    """
    if not await has_console_access(user):
        raise HTTPException(
            status_code=403,
            detail=(
                "No REC grants you access. Ask a REC administrator to add you to its "
                "Keycloak organization as a manager."
            ),
        )
    return user


async def require_community_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_community_read(user, community_key)
    if not decision.allowed:
        logger.warning(
            "Community read denied sub=%s community=%s reason=%s",
            user.sub,
            community_key,
            decision.reason,
        )
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_objectives_write(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_objectives_write(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_devices_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_devices_read(user, community_key)
    if not decision.allowed:
        logger.warning(
            "Device data denied sub=%s community=%s reason=%s",
            user.sub,
            community_key,
            decision.reason,
        )
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_flexibility_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_flexibility_read(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_gamification_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_gamification_read(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_nudging_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_nudging_read(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_alerts_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_alerts_read(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_alerts_write(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_alerts_write(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_members_read(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_members_read(user, community_key)
    if not decision.allowed:
        logger.warning(
            "Members read denied sub=%s community=%s reason=%s",
            user.sub,
            community_key,
            decision.reason,
        )
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


async def require_members_invite(
    community_key: str,
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    decision = await policy.allow_members_invite(user, community_key)
    if not decision.allowed:
        logger.warning(
            "Member email denied sub=%s community=%s reason=%s",
            user.sub,
            community_key,
            decision.reason,
        )
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
    return user


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
DbDep = Annotated[AsyncSession, Depends(get_db)]
DTDep = Annotated[DTClient, Depends(get_dt_client)]
RegistryDep = Annotated[RecRegistryAdminClient, Depends(get_registry_client)]
NudgingDep = Annotated[NudgingAdminClient, Depends(get_nudging_client)]
OnboardingDep = Annotated[OnboardingAdminClient, Depends(get_onboarding_client)]
