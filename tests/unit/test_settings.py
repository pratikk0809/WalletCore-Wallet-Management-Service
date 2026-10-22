"""Unit tests for the startup checks in Settings (no .env file, no database)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions import AuthenticationError


def make_settings(minimum: str, maximum: str) -> Settings:
    return Settings(
        _env_file=None,  # ignore the real .env, so the test is self-contained
        database_url="postgresql+psycopg://u:p@localhost/db",
        secret_key="k" * 32,
        min_transaction_amount=Decimal(minimum),
        max_transaction_amount=Decimal(maximum),
    )


def test_a_valid_range_is_accepted():
    settings = make_settings("0.01", "100000.00")

    assert settings.min_transaction_amount < settings.max_transaction_amount


@pytest.mark.parametrize(
    "minimum, maximum",
    [
        ("5.00", "5.00"),  # equal: nothing could ever be valid
        ("10.00", "5.00"),  # swapped
        ("0", "5.00"),  # zero minimum
        ("-1", "5.00"),  # negative minimum
    ],
)
def test_a_broken_range_stops_the_app_at_startup(minimum, maximum):
    with pytest.raises(ValidationError, match="MIN_TRANSACTION_AMOUNT"):
        make_settings(minimum, maximum)


def test_authentication_errors_carry_the_www_authenticate_header():
    error = AuthenticationError()

    assert error.status_code == 401
    assert error.headers == {"WWW-Authenticate": "Bearer"}
