"""Security-sensitive configuration tests."""

import pytest
from pydantic import ValidationError

from celine.community.settings import Settings


def test_development_authentication_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("DEV_AUTH_ENABLED", raising=False)
    assert Settings(_env_file=None).dev_auth_enabled is False


@pytest.mark.parametrize(
    ("celine_env", "environment"),
    [
        ("", ""),  # unset is hardened
        ("staging", ""),
        ("prod", ""),
        ("production", ""),
        ("", "development"),  # the old default name no longer relaxes
        ("", "test"),
        ("devel", "dev"),  # a typo in the first name is not rescued by the second
    ],
)
def test_development_authentication_is_refused_outside_dev(
    celine_env: str, environment: str
) -> None:
    with pytest.raises(ValidationError, match="DEV_AUTH_ENABLED"):
        Settings(
            _env_file=None,
            celine_env=celine_env,
            environment=environment,
            dev_auth_enabled=True,
        )


@pytest.mark.parametrize(
    ("celine_env", "environment"),
    [("dev", ""), ("DEV", ""), ("", "dev")],
)
def test_development_authentication_is_accepted_in_dev(celine_env: str, environment: str) -> None:
    result = Settings(
        _env_file=None,
        celine_env=celine_env,
        environment=environment,
        dev_auth_enabled=True,
    )
    assert result.is_dev is True


@pytest.mark.parametrize("value", ["production", "prod", "staging", "anything-else"])
def test_any_environment_value_is_accepted_and_hardened(value: str) -> None:
    """The old Literal rejected `prod`/`staging` at startup; now they are hardened."""
    result = Settings(_env_file=None, celine_env=value, dev_auth_enabled=False)

    assert result.hardened is True
    assert result.posture_env == value
