"""Authentication, authorization, and downstream client dependencies."""

import logging
from typing import Annotated

import jwt as pyjwt
from celine.sdk.auth import JwtUser, OidcClientCredentialsProvider
from celine.sdk.auth.jwt import Organization
from celine.sdk.dt import DTClient
from celine.sdk.nudging import NudgingAdminClient
from celine.sdk.rec_registry import RecRegistryAdminClient
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from celine.community.db import get_db
from celine.community.security.policy import policy
from celine.community.settings import settings

logger = logging.getLogger(__name__)

COMMUNITY_ORG_TYPES = {"rec", "community", "energy-community"}
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


def extract_token(request: Request) -> str | None:
    token = request.headers.get(settings.jwt_header_name)
    if token:
        return token
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _development_user() -> JwtUser:
    claims = {
        "sub": settings.dev_user_sub,
        "email": settings.dev_user_email,
        "name": settings.dev_user_name,
        "preferred_username": "community-manager-dev",
        "locale": "it",
        "groups": ["managers"],
        "scope": (
            "community.read community.devices.read community.nudging.read community.alerts.write"
        ),
        "organization": {
            settings.dev_community_key: {
                "attributes": {"type": ["community"]},
                "groups": ["managers"],
            }
        },
    }
    return JwtUser(
        sub=settings.dev_user_sub,
        email=settings.dev_user_email,
        name=settings.dev_user_name,
        preferred_username="community-manager-dev",
        organizations=[
            Organization(
                alias=settings.dev_community_key,
                attributes={"type": ["community"]},
            )
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


def _is_community_org(org: Organization) -> bool:
    return bool(
        org.type in COMMUNITY_ORG_TYPES
        or any(org.has_attribute("type", value) for value in COMMUNITY_ORG_TYPES)
    )


def resolve_user_community(user: JwtUser) -> str:
    matching = [org.alias for org in user.organizations if _is_community_org(org)]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(user.organizations) == 1:
        return user.organizations[0].alias
    if len(matching) > 1:
        raise HTTPException(status_code=403, detail="Multi-REC access is not supported in V1")
    raise HTTPException(status_code=403, detail="REC organization membership required")


async def require_console_access(
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    community_key = resolve_user_community(user)
    decision = await policy.allow_console(user, community_key)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "access denied")
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
DbDep = Annotated[AsyncSession, Depends(get_db)]
DTDep = Annotated[DTClient, Depends(get_dt_client)]
RegistryDep = Annotated[RecRegistryAdminClient, Depends(get_registry_client)]
NudgingDep = Annotated[NudgingAdminClient, Depends(get_nudging_client)]
