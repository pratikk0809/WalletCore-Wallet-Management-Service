"""Small time helpers. Pure functions."""

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """A datetime without a timezone is treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
