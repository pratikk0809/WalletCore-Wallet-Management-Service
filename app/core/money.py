"""Money validation helpers. Pure functions: no database, no FastAPI."""
from decimal import Decimal, InvalidOperation

from app.core.config import get_settings
from app.exceptions.errors import ValidationError

# Two decimal places, matching NUMERIC(18,2) in the database.
TWO_PLACES = Decimal("0.01")


def validate_amount(
    value: object,
    *,
    min_amount: Decimal | int | None = None,
    max_amount: Decimal | int | None = None,
) -> Decimal:
    """Check a money amount and return it as a Decimal with exactly 2 decimal places.

    Rules: a real number (not NaN/Infinity), greater than zero, within the
    per-transaction min/max, and at most 2 decimal places.
    Raises ValidationError (HTTP 422) on any violation.
    """
    settings = get_settings()
    min_amount = Decimal(
        settings.min_transaction_amount if min_amount is None else min_amount
    )
    max_amount = Decimal(
        settings.max_transaction_amount if max_amount is None else max_amount
    )

    # 1. Type: floats are inexact, and bool is a subclass of int, so reject both.
    if isinstance(value, float):
        raise ValidationError(
            "Amount must be sent as a decimal string (e.g. \"10.50\"), not a float."
        )
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValidationError("Amount must be a valid decimal number.")

    # 2. Parse.
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).strip())
        except InvalidOperation:
            raise ValidationError("Amount must be a valid decimal number.")

    # 3. NaN and Infinity are valid Decimals but would break every comparison below.
    if not amount.is_finite():
        raise ValidationError("Amount must be a finite number.")

    # 4. Positive, then within limits (before quantize, so huge numbers fail here).
    if amount <= 0:
        raise ValidationError("Amount must be greater than zero.")
    if amount < min_amount:
        raise ValidationError(f"Amount must be at least {min_amount}.")
    if amount > max_amount:
        raise ValidationError(f"Amount must be at most {max_amount}.")

    # 5. At most 2 decimal places. We reject (never silently round) 10.001.
    normalized = amount.quantize(TWO_PLACES)
    if normalized != amount:
        raise ValidationError("Amount can have at most 2 decimal places.")

    # "10", "10.5" and "10.500" all come back as 10.00, 10.50 and 10.50.
    return normalized