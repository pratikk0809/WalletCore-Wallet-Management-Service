"""Unit tests for AuditService (mocked repository)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.enums import AuditAction
from app.services.audit_service import AuditService


@pytest.fixture
def repo():
    repo = MagicMock()
    repo.add.side_effect = lambda entry: entry  # return what was staged
    return repo


@pytest.fixture
def service(repo):
    return AuditService(repo)


def test_record_stages_one_entry_with_all_fields(service, repo):
    actor, entity = uuid.uuid4(), uuid.uuid4()

    result = service.record(
        AuditAction.DEPOSIT_COMPLETED,
        entity_type="transaction",
        entity_id=entity,
        actor_id=actor,
        metadata={"wallet_id": "w-1"},
        ip_address="203.0.113.7",
    )

    repo.add.assert_called_once_with(result)
    assert result.action == "deposit_completed"
    assert result.entity_type == "transaction" and result.entity_id == entity
    assert result.actor_id == actor
    assert result.details == {"wallet_id": "w-1"}
    assert result.ip_address == "203.0.113.7"


def test_optional_fields_default_to_empty(service):
    result = service.record(AuditAction.LOGIN_FAILED, entity_type="user")

    assert result.actor_id is None and result.entity_id is None
    assert result.details == {} and result.ip_address is None


def test_values_are_made_json_safe(service):
    uid = uuid.uuid4()
    when = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    result = service.record(
        AuditAction.TRANSFER_COMPLETED,
        entity_type="transaction",
        metadata={"amount": Decimal("30.50"), "wallet": uid, "at": when},
    )

    assert result.details == {
        "amount": "30.50",
        "wallet": str(uid),
        "at": str(when),
    }


def test_sensitive_keys_are_redacted(service):
    result = service.record(
        AuditAction.USER_REGISTERED,
        entity_type="user",
        metadata={
            "password": "S3cret!pass",
            "access_token": "eyJ...",
            "Authorization": "Bearer abc",
            "user_email": "alice@example.com",
            "amount": "10.00",
        },
    )

    assert result.details["password"] == "[redacted]"
    assert result.details["access_token"] == "[redacted]"
    assert result.details["Authorization"] == "[redacted]"
    assert result.details["user_email"] == "[redacted]"
    assert result.details["amount"] == "10.00"  # harmless values stay


def test_nested_sensitive_keys_are_redacted_and_input_is_not_modified(service):
    original = {"request": {"password": "x", "amount": "5.00"}, "items": [{"token": "t"}]}

    result = service.record(
        AuditAction.LOGIN_FAILED, entity_type="user", metadata=original
    )

    assert result.details == {
        "request": {"password": "[redacted]", "amount": "5.00"},
        "items": [{"token": "[redacted]"}],
    }
    assert original["request"]["password"] == "x"  # caller's dict untouched