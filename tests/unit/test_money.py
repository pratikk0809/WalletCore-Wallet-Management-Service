"""Unit tests for app/core/money.py (no database needed)."""
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.core.money import validate_amount
from app.exceptions.errors import ValidationError

MIN = Decimal("0.01")
MAX = Decimal("100000.00")


def check(value):
    """validate_amount with fixed limits, so tests don't depend on your .env."""
    return validate_amount(value, min_amount=MIN, max_amount=MAX)


# ---------- accepted values (and how they are normalized) ----------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("10", "10.00"),
        ("10.5", "10.50"),
        ("10.50", "10.50"),
        ("10.500", "10.50"),   # trailing zero is harmless
        (Decimal("0.01"), "0.01"),
        (5, "5.00"),
        ("1E+2", "100.00"),    # scientific notation
    ],
)
def test_valid_amounts_are_normalized_to_two_places(value, expected):
    result = check(value)
    assert result == Decimal(expected)
    assert str(result) == expected


# ---------- rejected values ----------

@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-5",
        "0.00",
        "10.001",       # 3 decimal places
        "abc",
        "",
        "NaN",
        "Infinity",
        "-Infinity",
        10.5,           # float
        True,           # bool is technically an int
        None,
        "100000.01",    # above max
    ],
)
def test_invalid_amounts_are_rejected(value):
    with pytest.raises(ValidationError):
        check(value)


# ---------- boundaries and messages ----------

def test_minimum_boundary_is_accepted():
    assert check("0.01") == Decimal("0.01")


def test_maximum_boundary_is_accepted():
    assert check("100000.00") == Decimal("100000.00")


def test_below_minimum_message():
    with pytest.raises(ValidationError, match="at least"):
        check("0.005")


def test_above_maximum_message():
    with pytest.raises(ValidationError, match="at most 100000"):
        check("100000.01")


def test_too_many_decimals_message():
    with pytest.raises(ValidationError, match="2 decimal"):
        check("10.001")


def test_custom_limits_override_the_defaults():
    with pytest.raises(ValidationError):
        validate_amount("5", min_amount=10, max_amount=20)
    assert validate_amount("15", min_amount=10, max_amount=20) == Decimal("15.00")


def test_default_limits_come_from_settings():
    settings = get_settings()
    assert 0 < settings.min_transaction_amount < settings.max_transaction_amount

    assert validate_amount(settings.max_transaction_amount) == settings.max_transaction_amount
    with pytest.raises(ValidationError):
        validate_amount(settings.max_transaction_amount + Decimal("0.01"))