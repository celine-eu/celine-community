"""Authenticated manager profile routes."""

from celine.sdk.auth import organization_aliases, realm_roles
from fastapi import APIRouter, HTTPException, Request

from celine.community.api.deps import NO_REC_DETAIL, ConsoleUserDep, RegistryDep, refuse
from celine.community.api.schemas import CommunityAccess, MeResponse, MeUser
from celine.community.services.recs import RegistryUnavailable, accessible_recs

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
async def me(request: Request, user: ConsoleUserDep, registry: RegistryDep) -> MeResponse:
    """The caller's identity and their per-REC capabilities.

    `403` when they manage nothing. A valid token that grants nothing is not an
    authentication failure, and answering `200` with an empty list would send the
    dashboard back to a login it has already passed.
    """
    try:
        recs, registry_available = await accessible_recs(user, registry)
    except RegistryUnavailable as exc:
        # Not a 403. This caller's grant is platform-wide, so their REC list exists
        # nowhere but the registry; telling an administrator they have no access
        # when a downstream is down sends them to look in the wrong place.
        raise HTTPException(
            status_code=503,
            detail="The REC registry is unavailable, so the REC list cannot be resolved.",
        ) from exc

    if not recs:
        raise refuse("console.read", user, request, reason="no_rec", detail=NO_REC_DETAIL)

    claims = user.claims or {}
    return MeResponse(
        user=MeUser(
            sub=user.sub,
            email=user.email or "",
            name=user.name,
            preferred_username=user.preferred_username,
            locale=claims.get("locale"),
            organizations=organization_aliases(claims),
            platform_roles=realm_roles(claims),
            communities=[
                CommunityAccess(key=rec.key, name=rec.name, capabilities=list(rec.capabilities))
                for rec in recs
            ],
            scopes=_scopes(claims),
        ),
        registry_available=registry_available,
    )
