"""Deployment posture: only CELINE_ENV=dev relaxes (celine.sdk.posture).

Hardened is the default, so these tests pin the environment explicitly instead
of inheriting the suite-wide `CELINE_ENV=dev` from conftest.
"""

import pytest
from celine.sdk.posture import InsecureConfiguration
from celine.sdk.settings.models import OidcSettings
from fastapi import HTTPException
from starlette.requests import Request

from celine.community.api import deps
from celine.community.security.policy import CommunityAccessPolicy
from celine.community.settings import UPSTREAM_URLS, Settings, posture_guard, settings

HARDENED = ["", "staging"]

DEV_DATABASE_URL = (
    "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/celine_community"
)


@pytest.fixture
def no_oidc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BASE_URL", "JWKS_URI", "AUDIENCE", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.delenv(f"CELINE_OIDC_{name}", raising=False)


def shipped_defaults(celine_env: str) -> Settings:
    """The configuration a checkout runs with when nothing is set."""
    return Settings(
        _env_file=None,
        celine_env=celine_env,
        environment="",
        database_url=DEV_DATABASE_URL,
        oidc=OidcSettings(
            audience="svc-community",
            client_id="svc-community",
            client_secret="svc-community",
        ),
    )


#: In-cluster addresses, as the infra chart sets them.
DEPLOYED_UPSTREAMS = {
    "digital_twin_api_url": "http://digital-twin:8002",
    "rec_registry_url": "http://rec-registry:8004",
    "flexibility_api_url": "http://flexibility-api:8017",
    "nudging_api_url": "http://nudging:8016",
    "webapp_api_url": "http://webapp:8014",
    "roi_api_url": "http://roi:8000",
    "onboarding_url": "http://onboarding:8040",
}


def deployed(celine_env: str) -> Settings:
    return Settings(
        _env_file=None,
        celine_env=celine_env,
        environment="",
        **DEPLOYED_UPSTREAMS,
        database_url="postgresql+asyncpg://community:k3J9x-generated@db.example.org:5432/community",
        oidc=OidcSettings(
            base_url="https://auth.example.org/realms/celine",
            jwks_uri="https://auth.example.org/realms/celine/protocol/openid-connect/certs",
            audience="svc-community",
            client_id="svc-community",
            client_secret="a-real-generated-secret",
        ),
    )


# ---------------------------------------------------------------------------
# Startup guard
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("no_oidc_env")
@pytest.mark.parametrize("celine_env", HARDENED)
def test_hardened_refuses_every_shipped_default(celine_env: str) -> None:
    guard = posture_guard(shipped_defaults(celine_env))

    with pytest.raises(InsecureConfiguration) as raised:
        guard.enforce()

    message = str(raised.value)
    for setting in (
        "DATABASE_URL",
        "CELINE_OIDC_CLIENT_SECRET",
        "CELINE_OIDC_BASE_URL",
        "CELINE_OIDC_JWKS_URI",
        "DIGITAL_TWIN_API_URL",
        "REC_REGISTRY_URL",
        "FLEXIBILITY_API_URL",
        "NUDGING_API_URL",
        "WEBAPP_API_URL",
        "ROI_API_URL",
    ):
        assert setting in message


@pytest.mark.usefixtures("no_oidc_env")
@pytest.mark.parametrize("celine_env", HARDENED)
def test_hardened_refuses_each_default_on_its_own(celine_env: str) -> None:
    """Each registration stands alone: fixing the others does not hide it."""
    good = deployed(celine_env)
    cases = {
        "DATABASE_URL": good.model_copy(update={"database_url": DEV_DATABASE_URL}),
        "CELINE_OIDC_CLIENT_SECRET": good.model_copy(
            update={"oidc": good.oidc.model_copy(update={"client_secret": "svc-community"})}
        ),
        "CELINE_OIDC_BASE_URL": good.model_copy(
            update={"oidc": OidcSettings(**good.oidc.model_dump(exclude={"base_url"}))}
        ),
        "CELINE_OIDC_AUDIENCE": good.model_copy(
            update={"oidc": good.oidc.model_copy(update={"audience": None})}
        ),
        "DEV_AUTH_ENABLED": good.model_copy(update={"dev_auth_enabled": True}),
    }
    for setting, candidate in cases.items():
        guard = posture_guard(candidate)
        assert [v.setting for v in guard.violations] == [setting]
        with pytest.raises(InsecureConfiguration, match=setting):
            guard.enforce()


@pytest.mark.parametrize("celine_env", HARDENED)
@pytest.mark.parametrize(("setting", "field"), sorted(UPSTREAM_URLS.items()))
def test_hardened_refuses_an_upstream_left_on_the_local_stack(
    celine_env: str, setting: str, field: str
) -> None:
    """The feedback inbox answered 502 on staging because two of these were never set."""
    candidate = deployed(celine_env).model_copy(update={field: "http://host.docker.internal:8014/"})
    guard = posture_guard(candidate)

    assert [v.setting for v in guard.violations] == [setting]
    with pytest.raises(InsecureConfiguration, match=setting):
        guard.enforce()


@pytest.mark.parametrize("celine_env", HARDENED)
@pytest.mark.parametrize("field", ["webapp_api_url", "roi_api_url", "onboarding_url"])
@pytest.mark.parametrize("unset", [None, ""])
def test_an_unset_upstream_is_a_feature_turned_off(
    celine_env: str, field: str, unset: str | None
) -> None:
    guard = posture_guard(deployed(celine_env).model_copy(update={field: unset}))

    assert guard.violations == []


@pytest.mark.parametrize("celine_env", HARDENED)
def test_hardened_starts_with_a_real_configuration(celine_env: str) -> None:
    guard = posture_guard(deployed(celine_env))

    assert guard.violations == []
    guard.enforce()


@pytest.mark.usefixtures("no_oidc_env")
def test_dev_starts_with_the_shipped_defaults(caplog: pytest.LogCaptureFixture) -> None:
    guard = posture_guard(shipped_defaults("dev"))

    guard.enforce()  # warns, does not raise

    assert guard.violations
    assert "development setting(s) in use" in caplog.text


# ---------------------------------------------------------------------------
# Development authentication
# ---------------------------------------------------------------------------


def _anonymous_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/me", "headers": []})


@pytest.mark.parametrize("celine_env", HARDENED)
def test_hardened_never_returns_the_development_user(
    monkeypatch: pytest.MonkeyPatch, celine_env: str
) -> None:
    """Even if the flag reaches the running settings, the bypass stays shut."""
    monkeypatch.setattr(settings, "celine_env", celine_env)
    monkeypatch.setattr(settings, "environment", "")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)

    with pytest.raises(HTTPException) as raised:
        deps.get_user_from_request(_anonymous_request())

    assert raised.value.status_code == 401


def test_dev_returns_the_development_user_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "celine_env", "dev")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)

    user = deps.get_user_from_request(_anonymous_request())

    assert user.sub == settings.dev_user_sub


# ---------------------------------------------------------------------------
# Policy engine fails closed
# ---------------------------------------------------------------------------


class _BrokenEngine:
    def evaluate_decision(self, *_args, **_kwargs):
        raise RuntimeError("engine exploded")


@pytest.fixture
def dev_user():
    return deps._development_user()


@pytest.mark.parametrize("celine_env", HARDENED)
def test_hardened_refuses_to_start_without_policies(
    monkeypatch: pytest.MonkeyPatch, tmp_path, celine_env: str
) -> None:
    monkeypatch.setattr(settings, "celine_env", celine_env)
    monkeypatch.setattr(settings, "environment", "")
    monkeypatch.setattr(settings.policies, "policies_dir", tmp_path / "missing")

    with pytest.raises(RuntimeError, match="failed to load"):
        CommunityAccessPolicy()


@pytest.mark.parametrize("celine_env", HARDENED)
async def test_hardened_denies_when_the_engine_is_missing(
    monkeypatch: pytest.MonkeyPatch, dev_user, celine_env: str
) -> None:
    monkeypatch.setattr(settings, "celine_env", celine_env)
    monkeypatch.setattr(settings, "environment", "")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    engine = CommunityAccessPolicy.__new__(CommunityAccessPolicy)
    engine._engine = None

    decision = await engine.allow(dev_user, "community.read", "example-rec")

    assert decision.allowed is False


@pytest.mark.parametrize("celine_env", HARDENED)
async def test_hardened_denies_when_evaluation_errors(
    monkeypatch: pytest.MonkeyPatch, dev_user, celine_env: str
) -> None:
    monkeypatch.setattr(settings, "celine_env", celine_env)
    monkeypatch.setattr(settings, "environment", "")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    engine = CommunityAccessPolicy.__new__(CommunityAccessPolicy)
    engine._engine = _BrokenEngine()

    decision = await engine.allow(dev_user, "community.read", "example-rec")

    assert decision.allowed is False


async def test_dev_may_degrade_without_the_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path, dev_user
) -> None:
    monkeypatch.setattr(settings, "celine_env", "dev")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    monkeypatch.setattr(settings.policies, "policies_dir", tmp_path / "missing")

    degraded = CommunityAccessPolicy()  # warns, does not raise
    assert degraded._engine is None

    engine = CommunityAccessPolicy.__new__(CommunityAccessPolicy)
    engine._engine = None
    decision = await engine.allow(dev_user, "community.read", "example-rec")
    assert decision.allowed is True
