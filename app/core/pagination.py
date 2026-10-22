"""Cursor encoding for pagination. Pure functions: no DB, no FastAPI."""

import base64
import json
import uuid
from datetime import datetime

from app.exceptions.errors import ValidationError


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    """Pack the position of the last returned row into a URL-safe string."""
    raw = json.dumps({"t": created_at.isoformat(), "i": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Unpack a cursor. Any malformed value raises ValidationError (HTTP 422)."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)  # restore stripped padding
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at = datetime.fromisoformat(data["t"])
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        return created_at, uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError):
        raise ValidationError("Invalid cursor.")
