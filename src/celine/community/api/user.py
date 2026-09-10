"""Authenticated manager profile routes."""

from fastapi import APIRouter

from celine.community.api.deps import ConsoleUserDep, resolve_user_community
from celine.community.api.schemas import MeResponse, MeUser
from celine.community.settings import settings

router = APIRouter(prefix="/api", tags=["user"])


def _scopes(claims: dict) -> list[str]:
    raw = claims.get("scope", "")
    if isinstance(raw, str):
        return [value for value in raw.split() if value]
    return list(raw) if isinstance(raw, list) else []


@router.get("/ping", include_in_schema=False)
async def ping(user: ConsoleUserDep) -> dict[str, bool]:
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: ConsoleUserDep) -> MeResponse:
    community_key = resolve_user_community(user)
    community_name = (
        settings.dev_community_name
        if settings.dev_auth_enabled and community_key == settings.dev_community_key
        else community_key.replace("-", " ").title()
    )
    return MeResponse(
        user=MeUser(
            sub=user.sub,
            email=user.email or "",
            name=user.name,
            preferred_username=user.preferred_username,
            locale=user.claims.get("locale"),
            organization=community_key,
            community_key=community_key,
            community_name=community_name,
            scopes=_scopes(user.claims),
        )
    )
