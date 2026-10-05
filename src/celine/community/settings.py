"""Application settings for the Community Manager BFF."""

import os
from typing import Literal

from celine.sdk.posture import DEV, PostureGuard
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

    # Deployment posture (celine.sdk.posture): only `dev` relaxes; unset, empty,
    # `prod`, `staging`, `test` or a typo is hardened. CELINE_ENV wins over the
    # older ENVIRONMENT name, which is still accepted. Read through settings, not
    # only os.environ, so a `.env` file can carry it too. `task run` exports
    # CELINE_ENV=dev.
    celine_env: str = ""
    environment: str = ""
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
    dev_user_email: str = "manager@example.local"
    dev_user_name: str = "REC Manager"
    # Which branch of the policy the development fixture exercises: an
    # organization-scoped manager of one REC, or a `platform-admin` realm-role
    # holder who belongs to no organization and sees every REC the registry lists. The REC itself is not a
    # setting — the fixture belongs to a real Keycloak organization whose alias is
    # the registry key, so there is nothing left to override.
    dev_user_profile: Literal["manager", "admin"] = "manager"

    cors_origins: list[str] = ["http://localhost:3007", "http://community.celine.localhost"]

    digital_twin_api_url: str | None = "http://host.docker.internal:8002"
    digital_twin_scope: str | None = "digital-twin.values.read dataset.query"
    rec_registry_url: str | None = "http://host.docker.internal:8004"
    rec_registry_scope: str | None = "rec-registry.read"
    # Asked for only when a manager attaches or detaches a meter, never as part of
    # the default token: the Digital Twin forwards that one (ADR-0003). Empty turns
    # the meter action off, and `GET /api/me` then does not report `members.meter`.
    rec_registry_assets_write_scope: str | None = "rec-registry.assets.write"
    # Asked for only when a manager corrects a member's role or area, never as part of
    # the default token, for the same reason (ADR-0003). Empty turns the edit action
    # off, and `GET /api/me` then does not report `members.edit`.
    rec_registry_profile_write_scope: str | None = "rec-registry.members.profile.write"
    flexibility_api_url: str | None = "http://host.docker.internal:8017"
    nudging_api_url: str | None = "http://host.docker.internal:8016"
    webapp_api_url: str | None = "http://host.docker.internal:8014"
    roi_api_url: str | None = "http://host.docker.internal:8018"
    nudging_scope: str | None = "nudging.analytics.read"
    # Onboarding sends a member's invitation or password reset for this BFF: it is
    # the only service that may call the provisioning service. No default, because
    # it is an internal address and a default hostname would be wrong on every
    # other checkout. Unset, the send buttons are not offered.
    onboarding_url: str | None = None
    onboarding_scope: str = "onboarding.members.invite"
    # Asked for only when a REC admin releases a member, never as part of the email
    # token: a token that can send an invitation must not be able to end a
    # membership. An optional scope of the BFF's client. Empty turns the release
    # action off, and `GET /api/me` then does not report `members.release`.
    onboarding_release_scope: str | None = "onboarding.members.release"
    downstream_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    aggregate_cache_ttl_seconds: float = Field(default=30.0, gt=0, le=3600)

    policies: PoliciesSettings = PoliciesSettings()

    @property
    def posture_env(self) -> str:
        """The environment signal, lowercased: CELINE_ENV first, then ENVIRONMENT."""
        return (self.celine_env.strip() or self.environment.strip()).lower()

    @property
    def is_dev(self) -> bool:
        return self.posture_env == DEV

    @property
    def hardened(self) -> bool:
        return not self.is_dev

    @model_validator(mode="after")
    def development_authentication_is_dev_only(self) -> "Settings":
        if self.dev_auth_enabled and self.hardened:
            raise ValueError(
                "DEV_AUTH_ENABLED is development only: it requires CELINE_ENV=dev "
                f"(CELINE_ENV={self.posture_env or '<unset>'})"
            )
        return self


def posture_guard(settings: "Settings") -> PostureGuard:
    """Every development default this service ships, registered for refusal.

    Hardened (anything but CELINE_ENV=dev) `enforce()` raises with the full list;
    in dev it logs one warning. DEV_AUTH_ENABLED is refused by the settings
    validator already, and registered here as well so the list is complete.
    """
    guard = PostureGuard("community-api", env=settings.posture_env)
    guard.forbid_dev_database_url("DATABASE_URL", settings.database_url)
    guard.forbid_secret_equal_to_client_id(
        "CELINE_OIDC_CLIENT_SECRET", settings.oidc.client_id, settings.oidc.client_secret
    )
    guard.require_explicit_oidc(settings.oidc, require_audience=True)
    guard.forbid_true(
        "DEV_AUTH_ENABLED",
        settings.dev_auth_enabled,
        "Leave it unset; every request must carry a real token.",
    )
    return guard


settings = Settings()
