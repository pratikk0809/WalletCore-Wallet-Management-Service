"""Unit tests for AdminService and the admin dependency (mocked, no database)."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.api.deps import get_current_admin
from app.exceptions import ForbiddenError, WalletNotFoundError
from app.models.enums import AuditAction
from app.models.user import User
from app.models.wallet import Wallet
from app.services.admin_service import AdminService


@pytest.fixture
def calls():
    """Order in which the audit write and the commit happened."""
    return []


@pytest.fixture
def db(calls):
    db = MagicMock()
    db.commit.side_effect = lambda: calls.append("commit")
    return db


@pytest.fixture
def audit(calls):
    audit = MagicMock()
    audit.record.side_effect = lambda *args, **kwargs: calls.append("audit")
    return audit


@pytest.fixture
def wallet_repo():
    return MagicMock()


@pytest.fixture
def service(db, wallet_repo, audit):
    return AdminService(db, wallet_repo, audit)


def make_wallet(status: str = "active") -> Wallet:
    return Wallet(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        currency="INR",
        balance=Decimal("10.00"),
        status=status,
        version=1,
    )


# ---------- AdminService ----------


def test_freezing_an_active_wallet_changes_it_and_audits_before_committing(
    service, wallet_repo, audit, calls
):
    wallet, admin_id = make_wallet("active"), uuid.uuid4()
    wallet_repo.get_by_id_for_update.return_value = wallet

    result = service.set_wallet_frozen(admin_id, wallet.id, True, "fraud")

    assert result.changed is True
    assert wallet.status == "frozen" and wallet.version == 2
    assert calls == ["audit", "commit"]
    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.WALLET_FROZEN
    assert kwargs["entity_type"] == "wallet" and kwargs["entity_id"] == wallet.id
    assert kwargs["actor_id"] == admin_id
    assert kwargs["metadata"] == {
        "previous_status": "active",
        "new_status": "frozen",
        "reason": "fraud",
        "owner_user_id": wallet.user_id,
    }


def test_unfreezing_a_frozen_wallet(service, wallet_repo, audit):
    wallet = make_wallet("frozen")
    wallet_repo.get_by_id_for_update.return_value = wallet

    result = service.set_wallet_frozen(uuid.uuid4(), wallet.id, False)

    assert result.changed is True
    assert wallet.status == "active" and wallet.version == 2
    assert audit.record.call_args.args[0] == AuditAction.WALLET_UNFROZEN


@pytest.mark.parametrize("status, frozen", [("frozen", True), ("active", False)])
def test_repeating_the_same_change_does_nothing(service, db, wallet_repo, audit, status, frozen):
    wallet = make_wallet(status)
    wallet_repo.get_by_id_for_update.return_value = wallet

    result = service.set_wallet_frozen(uuid.uuid4(), wallet.id, frozen)

    assert result.changed is False
    assert wallet.status == status and wallet.version == 1
    audit.record.assert_not_called()
    db.commit.assert_not_called()


def test_unknown_wallet_raises_not_found(service, db, wallet_repo, audit):
    wallet_repo.get_by_id_for_update.return_value = None

    with pytest.raises(WalletNotFoundError):
        service.set_wallet_frozen(uuid.uuid4(), uuid.uuid4(), True)

    audit.record.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("given, stored", [("  fraud  ", "fraud"), ("   ", None), (None, None)])
def test_reason_is_trimmed_and_blank_becomes_none(service, wallet_repo, audit, given, stored):
    wallet_repo.get_by_id_for_update.return_value = make_wallet("active")

    service.set_wallet_frozen(uuid.uuid4(), uuid.uuid4(), True, given)

    assert audit.record.call_args.kwargs["metadata"]["reason"] == stored


# ---------- get_current_admin ----------


def test_admin_user_passes_the_admin_check():
    admin = User(id=uuid.uuid4(), email="a@example.com", password_hash="x", role="admin")

    assert get_current_admin(admin) is admin


def test_normal_user_is_rejected_by_the_admin_check():
    user = User(id=uuid.uuid4(), email="u@example.com", password_hash="x", role="user")

    with pytest.raises(ForbiddenError):
        get_current_admin(user)
