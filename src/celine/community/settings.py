"""Application settings for the Community Manager BFF."""

import os
from typing import Literal

from celine.sdk.settings.models import OidcSettings, PoliciesSettings
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = 8019

    database_url: str = (
        "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/"
        "celine_community"
    )
    database_echo: bool = False

    oidc: OidcSettings = OidcSettings(
        audience=os.getenv("CELINE_OIDC_AUDIENCE", "svc-community"),
        client_id=os.getenv("CELINE_OIDC_CLIENT_ID", "svc-community"),
        client_secret=os.getenv("CELINE_OIDC_CLIENT_SECRET", "svc-community"),
    )
    jwt_header_name: str = "x-auth-request-access-token"
    # Opt-in only: the normal local path exercises the same signed identity and
    # per-REC role boundary as a deployment.
    dev_auth_enabled: bool = False
    dev_user_sub: str = "community-manager-dev"
    dev_user_email: str = "manager@greenland.local"
    dev_user_name: str = "REC Manager"
    # Which branch of the policy the development fixture exercises: an
    # organization-scoped manager of one REC, or a realm admin who belongs to no
    # organization and sees every REC the registry lists. The REC itself is not a
    # setting — the fixture belongs to a real Keycloak organization whose alias is
    # the registry key, so there is nothing left to override.
    dev_user_profile: Literal["manager", "admin"] = "manager"

    cors_origins: list[str] = ["http://localhost:3007", "http://community.celine.localhost"]

    digital_twin_api_url: str | None = "http://host.docker.internal:8002"
    digital_twin_scope: str | None = "digital-twin.values.read dataset.query"
    rec_registry_url: str | None = "http://host.docker.internal:8004"
    rec_registry_scope: str | None = "rec-registry.read"
    flexibility_api_url: str | None = "http://host.docker.internal:8017"
    nudging_api_url: str | None = "http://host.docker.internal:8016"
    webapp_api_url: str | None = "http://host.docker.internal:8014"
    nudging_scope: str | None = "nudging.analytics.read"
    # Onboarding sends a member's invitation or password reset for this BFF: it is
    # the only service that may call the provisioning service. No default, because
    # it is an internal address and a default hostname would be wrong on every
    # other checkout. Unset, the send buttons are not offered.
    onboarding_url: str | None = None
    onboarding_scope: str = "onboarding.members.invite"
    downstream_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    aggregate_cache_ttl_seconds: float = Field(default=30.0, gt=0, le=3600)

    policies: PoliciesSettings = PoliciesSettings()

    @model_validator(mode="after")
    def production_is_fail_closed(self) -> "Settings":
        if self.environment == "production" and self.dev_auth_enabled:
            raise ValueError("DEV_AUTH_ENABLED must be false in production")
        return self


settings = Settings()
