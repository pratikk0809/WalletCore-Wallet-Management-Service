"""Unit tests for app/core/security.py."""

from datetime import timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.exceptions.errors import AuthenticationError

settings = get_settings()


def test_hash_is_not_plaintext_and_verifies():
    hashed = hash_password("S3cret!pass")
    assert hashed != "S3cret!pass"
    assert verify_password("S3cret!pass", hashed) is True


def test_wrong_password_is_rejected():
    hashed = hash_password("S3cret!pass")
    assert verify_password("wrong-password", hashed) is False


def test_same_password_gives_different_hashes():
    # Different random salts -> different hashes, both still valid.
    first = hash_password("S3cret!pass")
    second = hash_password("S3cret!pass")
    assert first != second
    assert verify_password("S3cret!pass", second) is True


def test_token_roundtrip_returns_subject():
    token = create_access_token("user-123")
    assert decode_access_token(token) == "user-123"


def test_expired_token_is_rejected():
    token = create_access_token("user-123", expires_delta=timedelta(seconds=-1))
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {"sub": "user-123", "exp": 9999999999},
        "a-completely-different-secret-key-0123456789",
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(forged)


def test_token_without_expiry_is_rejected():
    no_exp = jwt.encode({"sub": "user-123"}, settings.secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(AuthenticationError):
        decode_access_token(no_exp)


def test_garbage_token_is_rejected():
    with pytest.raises(AuthenticationError):
        decode_access_token("not-a-jwt")
