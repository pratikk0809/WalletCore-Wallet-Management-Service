"""Unit tests for the request fingerprint."""

from decimal import Decimal

from app.core.idempotency import compute_request_hash

AMOUNT = Decimal("10.00")


def test_same_request_gives_same_64_char_hash():
    first = compute_request_hash("deposit", AMOUNT, "rent")
    second = compute_request_hash("deposit", AMOUNT, "rent")
    assert first == second
    assert len(first) == 64


def test_different_amount_changes_hash():
    assert compute_request_hash("deposit", AMOUNT, None) != compute_request_hash(
        "deposit", Decimal("10.01"), None
    )


def test_different_reference_changes_hash():
    assert compute_request_hash("deposit", AMOUNT, "a") != compute_request_hash(
        "deposit", AMOUNT, "b"
    )


def test_different_type_changes_hash():
    assert compute_request_hash("deposit", AMOUNT, None) != compute_request_hash(
        "withdrawal", AMOUNT, None
    )
