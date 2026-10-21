"""Unit tests for TransactionService.deposit. Repositories and DB are mocked."""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.idempotency import compute_request_hash
from app.exceptions import (
    DuplicateTransactionError,
    ValidationError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.services.transaction_service import TransactionService


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def wallet_repo():
    return MagicMock()


@pytest.fixture
def txn_repo():
    repo = MagicMock()
    repo.get_by_user_and_key.return_value = None  # key not used yet
    return repo


@pytest.fixture
def ledger_repo():
    return MagicMock()

@pytest.fixture
def limit_service():
    return MagicMock()  # never raises, so the old tests are unaffected


@pytest.fixture
def service(db, wallet_repo, txn_repo, ledger_repo, limit_service):
    return TransactionService(db, wallet_repo, txn_repo, ledger_repo, limit_service, MagicMock())


def make_wallet(balance: str = "5.00", status: str = "active") -> Wallet:
    return Wallet(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        currency="INR",
        balance=Decimal(balance),
        status=status,
        version=1,
    )


def make_existing(wallet: Wallet, amount: str = "10.00") -> Transaction:
    value = Decimal(amount)
    return Transaction(
        id=uuid.uuid4(),
        user_id=wallet.user_id,
        type="deposit",
        status="completed",
        amount=value,
        currency="INR",
        destination_wallet_id=wallet.id,
        idempotency_key="key-1",
        request_hash=compute_request_hash("deposit", value, None),
    )


def test_deposit_increases_balance_and_bumps_version(service, wallet_repo):
    wallet = make_wallet("5.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    assert wallet.balance == Decimal("15.00")
    assert wallet.version == 2


def test_deposit_writes_balanced_ledger_entries(service, wallet_repo, ledger_repo):
    wallet = make_wallet("5.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    entries = ledger_repo.add_many.call_args.args[0]
    credits = [e for e in entries if e.direction == "credit"]
    debits = [e for e in entries if e.direction == "debit"]
    assert len(credits) == len(debits) == 1
    assert sum(e.amount for e in credits) == sum(e.amount for e in debits)
    assert credits[0].wallet_id == wallet.id
    assert credits[0].balance_after == Decimal("15.00")
    assert debits[0].wallet_id is None and debits[0].balance_after is None


def test_deposit_commits_once_and_is_marked_completed(service, db, wallet_repo, txn_repo):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    result = service.deposit(wallet.user_id, "key-1", Decimal("10.00"), "  top-up ")

    assert result.created is True
    assert result.transaction.status == "completed"
    assert result.transaction.type == "deposit"
    assert result.transaction.reference == "top-up"  # trimmed
    assert result.transaction.destination_wallet_id == wallet.id
    assert result.transaction.source_wallet_id is None
    db.commit.assert_called_once()


def test_replay_returns_original_without_writing(
    service, db, wallet_repo, txn_repo, ledger_repo
):
    wallet = make_wallet("20.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    existing = make_existing(wallet)
    txn_repo.get_by_user_and_key.return_value = existing

    result = service.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    assert result.created is False
    assert result.transaction is existing
    assert wallet.balance == Decimal("20.00")  # unchanged
    ledger_repo.add_many.assert_not_called()
    db.commit.assert_not_called()


def test_same_key_with_different_amount_raises_conflict(
    service, db, wallet_repo, txn_repo
):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    txn_repo.get_by_user_and_key.return_value = make_existing(wallet, "10.00")

    with pytest.raises(DuplicateTransactionError):
        service.deposit(wallet.user_id, "key-1", Decimal("99.00"))

    db.commit.assert_not_called()


def test_frozen_wallet_rejects_deposit(service, db, wallet_repo, ledger_repo):
    wallet = make_wallet("5.00", status="frozen")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    with pytest.raises(WalletFrozenError):
        service.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    assert wallet.balance == Decimal("5.00")
    ledger_repo.add_many.assert_not_called()
    db.commit.assert_not_called()


def test_missing_wallet_raises(service, wallet_repo):
    wallet_repo.get_by_user_id_for_update.return_value = None

    with pytest.raises(WalletNotFoundError):
        service.deposit(uuid.uuid4(), "key-1", Decimal("10.00"))


def test_invalid_amount_is_rejected_before_touching_the_database(service, wallet_repo):
    with pytest.raises(ValidationError):
        service.deposit(uuid.uuid4(), "key-1", Decimal("10.001"))

    wallet_repo.get_by_user_id_for_update.assert_not_called()


def test_unique_key_race_is_treated_as_replay(service, db, wallet_repo, txn_repo):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    existing = make_existing(wallet)
    # First lookup: nothing. Insert hits the unique constraint. Second lookup: found.
    txn_repo.get_by_user_and_key.side_effect = [None, existing]
    txn_repo.add.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    result = service.deposit(wallet.user_id, "key-1", Decimal("10.00"))

    assert result.created is False
    assert result.transaction is existing
    db.rollback.assert_called_once()
    db.commit.assert_not_called()