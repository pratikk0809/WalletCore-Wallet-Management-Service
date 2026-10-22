"""Unit tests for the documented error responses (no database)."""

import pytest

from app.api.responses import errors
from app.schemas.errors import ErrorResponse


def test_401_documents_the_www_authenticate_header():
    response = errors(401)[401]

    assert "WWW-Authenticate" in response["headers"]


def test_429_documents_the_retry_after_header():
    response = errors(429)[429]

    assert response["headers"]["Retry-After"]["schema"] == {"type": "integer"}


def test_custom_entries_replace_the_defaults():
    response = errors(403, custom={403: ("Frozen.", "WALLET_FROZEN", "Wallet is frozen.")})[403]

    assert response["description"] == "Frozen."
    example = response["content"]["application/json"]["example"]
    assert example["error"]["code"] == "WALLET_FROZEN"


def test_every_error_uses_the_standard_error_model():
    for status, response in errors(401, 403, 404, 409, 422, 429).items():
        assert response["model"] is ErrorResponse, status


def test_the_422_example_includes_details():
    example = errors(422)[422]["content"]["application/json"]["example"]

    assert example["error"]["details"][0]["field"]


def test_an_unknown_status_without_a_custom_entry_is_an_error():
    with pytest.raises(KeyError):
        errors(418)
