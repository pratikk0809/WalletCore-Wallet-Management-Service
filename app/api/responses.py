"""Documented error responses for the OpenAPI schema. Documentation only: nothing here
changes behavior."""
from typing import Any

from app.schemas.errors import ErrorResponse
from app.schemas.transaction import TransactionResponse

_EXAMPLE_REQUEST_ID = "9f1c2b7a4d3e4f5a8b6c7d8e9f0a1b2c"

# status -> (description, error code, example message). Routes can override any of them.
_DEFAULTS: dict[int, tuple[str, str, str]] = {
    401: ("Missing, invalid or expired token.", "UNAUTHORIZED", "Not authenticated."),
    403: ("Not allowed.", "FORBIDDEN", "Admin access required."),
    404: ("Not found.", "NOT_FOUND", "Not Found"),
    409: (
        "The Idempotency-Key was already used for a different request.",
        "DUPLICATE_TRANSACTION",
        "This idempotency key was already used for a different request.",
    ),
    422: (
        "Invalid request: bad input or a business rule failed.",
        "VALIDATION_ERROR",
        "Request validation failed.",
    ),
    429: (
        "Too many requests. Wait `Retry-After` seconds.",
        "RATE_LIMITED",
        "Too many requests. Try again in 30 seconds.",
    ),
}

_HEADERS: dict[int, dict[str, Any]] = {
    401: {"WWW-Authenticate": {"description": "Always `Bearer`.", "schema": {"type": "string"}}},
    429: {
        "Retry-After": {
            "description": "Seconds to wait before retrying.",
            "schema": {"type": "integer"},
        }
    },
}


def _build(status: int, description: str, code: str, message: str) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": _EXAMPLE_REQUEST_ID,
    }
    if status == 422:
        error["details"] = [{"field": "amount", "message": "Input should be a valid decimal"}]
    response: dict[str, Any] = {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": {"error": error}}},
    }
    if status in _HEADERS:
        response["headers"] = _HEADERS[status]
    return response


def errors(
    *statuses: int, custom: dict[int, tuple[str, str, str]] | None = None
) -> dict[int, dict[str, Any]]:
    """Documented error responses for a route, e.g. `errors(401, 404, 422)`.

    `custom` replaces the default (description, code, message) of a status.
    An unknown status without a custom entry raises KeyError at import time."""
    table = {**_DEFAULTS, **(custom or {})}
    return {status: _build(status, *table[status]) for status in statuses}


# Documents the "200 = replay" outcome of the idempotent money endpoints.
REPLAYED: dict[int, dict[str, Any]] = {
    200: {
        "model": TransactionResponse,
        "description": (
            "Replay: this Idempotency-Key and body were already processed. "
            "The ORIGINAL transaction is returned and nothing new happens."
        ),
        "headers": {
            "Idempotent-Replayed": {"description": "Always `true`.", "schema": {"type": "string"}}
        },
    }
}