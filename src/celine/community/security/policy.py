"""Local OPA policy evaluation for the Community Manager BFF."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from celine.sdk.auth import JwtUser
from celine.sdk.auth.jwt import extract_groups
from fastapi import HTTPException

from celine.community.settings import settings

logger = logging.getLogger(__name__)

_PACKAGE = "celine.community.access"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None


def _community_key(user: JwtUser) -> str | None:
    from celine.community.api.deps import resolve_user_community

    try:
        return resolve_user_community(user)
    except HTTPException:
        return None


def _policy_input(user: JwtUser, action: str, community_key: str):
    from celine.sdk.policies import (
        Action,
        PolicyInput,
        Resource,
        ResourceType,
        Subject,
        SubjectType,
    )

    raw_scope = user.claims.get("scope", "")
    scopes = raw_scope.split() if isinstance(raw_scope, str) else list(raw_scope or [])
    subject_type = (
        SubjectType.SERVICE
        if user.is_service_account and not user.organizations
        else SubjectType.USER
    )
    return PolicyInput(
        subject=Subject(
            id=user.sub,
            type=subject_type,
            groups=extract_groups(user.claims),
            scopes=scopes,
            claims={
                "community_key": None
                if subject_type == SubjectType.SERVICE
                else _community_key(user)
            },
        ),
        resource=Resource(
            type=ResourceType.USERDATA,
            id=community_key,
            attributes={"community_key": community_key},
        ),
        action=Action(name=action),
    )


class CommunityAccessPolicy:
    def __init__(self) -> None:
        self._engine = None
        try:
            from celine.sdk.policies import PolicyEngine

            policies_dir = settings.policies.policies_dir
            self._engine = PolicyEngine(policies_dir=str(policies_dir))
            self._engine.load()
            logger.info("Community access policy loaded from %s", policies_dir)
        except Exception as exc:
            if settings.environment == "production":
                raise RuntimeError("Community policies failed to load") from exc
            logger.warning("Community policy unavailable in development: %s", exc)

    async def _evaluate(self, user: JwtUser, action: str, community_key: str) -> Decision:
        if self._engine is None:
            return Decision(settings.dev_auth_enabled, "development policy fallback")
        try:
            result = self._engine.evaluate_decision(
                _PACKAGE,
                _policy_input(user, action, community_key),
            )
            return Decision(result.allowed, result.reason or None)
        except Exception as exc:
            logger.exception("Policy evaluation failed")
            if settings.environment == "production":
                return Decision(False, "policy evaluation failed")
            return Decision(settings.dev_auth_enabled, f"development policy fallback: {exc}")

    async def allow_console(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "console.read", community_key)

    async def allow_community_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "community.read", community_key)

    async def allow_objectives_write(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "objectives.write", community_key)

    async def allow_devices_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "devices.read", community_key)

    async def allow_flexibility_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "flexibility.read", community_key)

    async def allow_gamification_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "gamification.read", community_key)

    async def allow_nudging_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "nudging.read", community_key)

    async def allow_alerts_read(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "alerts.read", community_key)

    async def allow_alerts_write(self, user: JwtUser, community_key: str) -> Decision:
        return await self._evaluate(user, "alerts.write", community_key)


policy = CommunityAccessPolicy()
