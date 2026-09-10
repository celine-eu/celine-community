"""Security-sensitive configuration tests."""

import pytest
from pydantic import ValidationError

from celine.community.settings import Settings


def test_production_refuses_development_authentication() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            dev_auth_enabled=True,
        )


def test_production_accepts_fail_closed_configuration() -> None:
    result = Settings(
        environment="production",
        dev_auth_enabled=False,
    )

    assert result.environment == "production"
