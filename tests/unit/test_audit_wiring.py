"""Unit tests: audit logging is wired into AuthService and TransactionService."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.idempotency import compute_request_hash
from app.core.request_context import client_ip_ctx
from app.core.security import hash_password
from app.exceptions import (
    AuthenticationError,
    EmailAlreadyRegisteredError,
    InsufficientFundsError,
)
from app.models.enums import AuditAction
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.transaction_service import TransactionService


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


# ---------- AuthService ----------


@pytest.fixture
def user_repo():
    return MagicMock()


@pytest.fixture
def auth(db, user_repo, audit):
    return AuthService(db, user_repo, MagicMock(), audit)


def make_user(status: str = "active") -> User:
    return User(
        id=uuid.uuid4(),
        email="alice@example.com",
        password_hash=hash_password("S3cret!pass"),
        status=status,
    )


def test_register_records_an_audit_row_before_committing(auth, user_repo, audit, calls):
    user_repo.get_by_email.return_value = None

    user = auth.register("alice@example.com", "S3cret!pass")

    assert calls == ["audit", "commit"]
    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.USER_REGISTERED
    assert kwargs["entity_type"] == "user"
    assert kwargs["entity_id"] == user.id and kwargs["actor_id"] == user.id


def test_register_with_a_taken_email_records_nothing(auth, user_repo, audit):
    user_repo.get_by_email.return_value = make_user()

    with pytest.raises(EmailAlreadyRegisteredError):
        auth.register("alice@example.com", "S3cret!pass")

    audit.record.assert_not_called()


def test_successful_login_is_audited_and_committed(auth, user_repo, audit, calls):
    user = make_user()
    user_repo.get_by_email.return_value = user

    auth.login("alice@example.com", "S3cret!pass")

    assert calls == ["audit", "commit"]
    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.LOGIN_SUCCEEDED
    assert kwargs["actor_id"] == user.id


def test_wrong_password_is_audited_and_committed_before_the_error(auth, user_repo, audit, calls):
    user = make_user()
    user_repo.get_by_email.return_value = user

    with pytest.raises(AuthenticationError):
        auth.login("alice@example.com", "wrong-password")

    assert calls == ["audit", "commit"]  # committed, or the rollback would erase it
    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.LOGIN_FAILED
    assert kwargs["actor_id"] == user.id
    assert kwargs["metadata"] == {"reason": "wrong_password"}


def test_unknown_email_is_audited_without_actor_and_without_the_email(auth, user_repo, audit):
    user_repo.get_by_email.return_value = None

    with pytest.raises(AuthenticationError):
        auth.login("nobody@example.com", "whatever123")

    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.LOGIN_FAILED
    assert kwargs["actor_id"] is None and kwargs["entity_id"] is None
    assert kwargs["metadata"] == {"reason": "unknown_email"}
    assert "nobody@example.com" not in str(audit.record.call_args)


def test_disabled_account_login_is_audited(auth, user_repo, audit):
    user_repo.get_by_email.return_value = make_user(status="disabled")

    with pytest.raises(AuthenticationError):
        auth.login("alice@example.com", "S3cret!pass")

    assert audit.record.call_args.kwargs["metadata"] == {"reason": "account_disabled"}


# ---------- TransactionService ----------


@pytest.fixture
def wallet_repo():
    return MagicMock()


@pytest.fixture
def txn_repo():
    repo = MagicMock()
    repo.get_by_user_and_key.return_value = None
    return repo


@pytest.fixture
def txns(db, wallet_repo, txn_repo, audit):
    return TransactionService(db, wallet_repo, txn_repo, MagicMock(), MagicMock(), audit)


def make_wallet(balance: str = "100.00") -> Wallet:
    return Wallet(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        currency="INR",
        balance=Decimal(balance),
        status="active",
        version=1,
    )


def test_deposit_records_an_audit_row_before_committing(txns, wallet_repo, audit, calls):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    result = txns.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    assert calls == ["audit", "commit"]
    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.DEPOSIT_COMPLETED
    assert kwargs["entity_type"] == "transaction"
    assert kwargs["entity_id"] == result.transaction.id
    assert kwargs["actor_id"] == wallet.user_id
    assert kwargs["metadata"]["amount"] == Decimal("10.00")
    assert kwargs["metadata"]["destination_wallet_id"] == wallet.id
    assert kwargs["metadata"]["source_wallet_id"] is None


def test_withdrawal_records_an_audit_row(txns, wallet_repo, audit):
    wallet = make_wallet("100.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    txns.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.WITHDRAWAL_COMPLETED
    assert kwargs["metadata"]["source_wallet_id"] == wallet.id


def test_transfer_records_one_audit_row_with_both_wallets(txns, wallet_repo, audit):
    source, destination = make_wallet("100.00"), make_wallet("5.00")
    wallet_repo.get_by_user_id.return_value = source
    wallet_repo.get_by_ids_for_update.return_value = [source, destination]

    txns.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    audit.record.assert_called_once()
    args, kwargs = audit.record.call_args
    assert args[0] == AuditAction.TRANSFER_COMPLETED
    assert kwargs["actor_id"] == source.user_id  # the sender, not the recipient
    assert kwargs["metadata"]["source_wallet_id"] == source.id
    assert kwargs["metadata"]["destination_wallet_id"] == destination.id


def test_replay_records_nothing(txns, wallet_repo, txn_repo, audit):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    txn_repo.get_by_user_and_key.return_value = Transaction(
        id=uuid.uuid4(),
        user_id=wallet.user_id,
        type="deposit",
        status="completed",
        amount=Decimal("10.00"),
        currency="INR",
        destination_wallet_id=wallet.id,
        idempotency_key="key-1",
        request_hash=compute_request_hash("deposit", Decimal("10.00"), None),
    )

    result = txns.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    assert result.created is False
    audit.record.assert_not_called()


def test_rejected_operation_records_nothing(txns, wallet_repo, audit):
    wallet = make_wallet("5.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    with pytest.raises(InsufficientFundsError):
        txns.withdraw(wallet.user_id, "key-1", Decimal("10.00"))

    audit.record.assert_not_called()


def test_if_the_audit_write_fails_nothing_is_committed(txns, db, wallet_repo, audit):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    audit.record.side_effect = RuntimeError("audit table unavailable")

    with pytest.raises(RuntimeError):
        txns.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    db.commit.assert_not_called()


# ---------- AuditService: client IP ----------


def test_record_uses_the_client_ip_from_the_request_context():
    repo = MagicMock()
    repo.add.side_effect = lambda entry: entry

    token = client_ip_ctx.set("198.51.100.4")
    try:
        entry = AuditService(repo).record(AuditAction.LOGIN_FAILED, entity_type="user")
    finally:
        client_ip_ctx.reset(token)

    assert entry.ip_address == "198.51.100.4"


def test_an_explicit_ip_overrides_the_request_context():
    repo = MagicMock()
    repo.add.side_effect = lambda entry: entry

    token = client_ip_ctx.set("198.51.100.4")
    try:
        entry = AuditService(repo).record(
            AuditAction.LOGIN_FAILED, entity_type="user", ip_address="203.0.113.9"
        )
    finally:
        client_ip_ctx.reset(token)

    assert entry.ip_address == "203.0.113.9"
