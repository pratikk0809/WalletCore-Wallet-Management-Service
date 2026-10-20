"""Unit tests for cursor encoding (no database needed)."""
import base64
import uuid
from datetime import datetime, timezone

import pytest

from app.core.pagination import decode_cursor, encode_cursor
from app.exceptions import ValidationError


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_cursor_roundtrip_keeps_timestamp_and_id():
    created_at = datetime(2026, 10, 19, 10, 15, 0, 123456, tzinfo=timezone.utc)
    row_id = uuid.uuid4()

    assert decode_cursor(encode_cursor(created_at, row_id)) == (created_at, row_id)


def test_cursor_is_url_safe():
    cursor = encode_cursor(datetime.now(timezone.utc), uuid.uuid4())

    assert not set(cursor) & set("=+/ ")


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "garbage",
        b64("not json"),
        b64("[1, 2]"),
        b64('{"t": "2026-01-01T00:00:00+00:00"}'),  # missing id
        b64(f'{{"t": "2026-01-01T00:00:00", "i": "{uuid.uuid4()}"}}'),  # no timezone
    ],
)
def test_malformed_cursors_are_rejected(cursor):
    with pytest.raises(ValidationError, match="Invalid cursor"):
        decode_cursor(cursor)