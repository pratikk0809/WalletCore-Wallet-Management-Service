"""Unit tests for TransactionService.withdraw. Repositories and DB are mocked."""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.idempotency import compute_request_hash
from app.exceptions import (
    DuplicateTransactionError,
    InsufficientFundsError,
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


def make_wallet(balance: str = "100.00", status: str = "active") -> Wallet:
    return Wallet(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        currency="INR",
        balance=Decimal(balance),
        status=status,
        version=1,
    )


def make_existing(wallet: Wallet, amount: str = "30.00", txn_type: str = "withdrawal"):
    value = Decimal(amount)
    return Transaction(
        id=uuid.uuid4(),
        user_id=wallet.user_id,
        type=txn_type,
        status="completed",
        amount=value,
        currency="INR",
        source_wallet_id=wallet.id if txn_type == "withdrawal" else None,
        destination_wallet_id=wallet.id if txn_type == "deposit" else None,
        idempotency_key="key-1",
        request_hash=compute_request_hash(txn_type, value, None),
    )


def test_withdraw_decreases_balance_and_bumps_version(service, wallet_repo):
    wallet = make_wallet("100.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    assert wallet.balance == Decimal("70.00")
    assert wallet.version == 2


def test_withdraw_writes_balanced_ledger_entries(service, wallet_repo, ledger_repo):
    wallet = make_wallet("100.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    entries = ledger_repo.add_many.call_args.args[0]
    debits = [e for e in entries if e.direction == "debit"]
    credits = [e for e in entries if e.direction == "credit"]
    assert len(debits) == len(credits) == 1
    assert sum(e.amount for e in debits) == sum(e.amount for e in credits)
    # Wallet side is the DEBIT (money out), with a balance snapshot.
    assert debits[0].wallet_id == wallet.id
    assert debits[0].balance_after == Decimal("70.00")
    # External side is the CREDIT.
    assert credits[0].wallet_id is None and credits[0].balance_after is None


def test_withdraw_commits_once_and_sets_source_wallet(service, db, wallet_repo):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    result = service.withdraw(wallet.user_id, "key-1", Decimal("30.00"), " rent ")

    assert result.created is True
    assert result.transaction.type == "withdrawal"
    assert result.transaction.status == "completed"
    assert result.transaction.reference == "rent"
    assert result.transaction.source_wallet_id == wallet.id
    assert result.transaction.destination_wallet_id is None
    db.commit.assert_called_once()


def test_withdrawing_the_exact_balance_is_allowed(service, wallet_repo):
    wallet = make_wallet("30.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    assert wallet.balance == Decimal("0.00")


def test_insufficient_funds_raises_and_changes_nothing(
    service, db, wallet_repo, ledger_repo
):
    wallet = make_wallet("20.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    with pytest.raises(InsufficientFundsError):
        service.withdraw(wallet.user_id, "key-1", Decimal("20.01"))

    assert wallet.balance == Decimal("20.00")
    assert wallet.version == 1
    ledger_repo.add_many.assert_not_called()
    db.commit.assert_not_called()


def test_frozen_wallet_rejects_withdrawal(service, db, wallet_repo):
    wallet = make_wallet("100.00", status="frozen")
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    with pytest.raises(WalletFrozenError):
        service.withdraw(wallet.user_id, "key-1", Decimal("10.00"))

    assert wallet.balance == Decimal("100.00")
    db.commit.assert_not_called()


def test_replay_returns_original_even_if_balance_is_now_too_low(
    service, db, wallet_repo, txn_repo, ledger_repo
):
    wallet = make_wallet("5.00")  # less than the original 30.00 withdrawal
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    existing = make_existing(wallet, "30.00")
    txn_repo.get_by_user_and_key.return_value = existing

    result = service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    assert result.created is False
    assert result.transaction is existing
    assert wallet.balance == Decimal("5.00")  # unchanged
    ledger_repo.add_many.assert_not_called()
    db.commit.assert_not_called()


def test_same_key_with_different_amount_raises_conflict(
    service, db, wallet_repo, txn_repo
):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    txn_repo.get_by_user_and_key.return_value = make_existing(wallet, "30.00")

    with pytest.raises(DuplicateTransactionError):
        service.withdraw(wallet.user_id, "key-1", Decimal("40.00"))

    db.commit.assert_not_called()


def test_key_first_used_for_a_deposit_conflicts_with_a_withdrawal(
    service, db, wallet_repo, txn_repo
):
    # Same key, same amount, but the type differs -> not a replay.
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    txn_repo.get_by_user_and_key.return_value = make_existing(
        wallet, "30.00", txn_type="deposit"
    )

    with pytest.raises(DuplicateTransactionError):
        service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    db.commit.assert_not_called()


def test_invalid_amount_is_rejected_before_touching_the_database(service, wallet_repo):
    with pytest.raises(ValidationError):
        service.withdraw(uuid.uuid4(), "key-1", Decimal("-5"))

    wallet_repo.get_by_user_id_for_update.assert_not_called()


def test_missing_wallet_raises(service, wallet_repo):
    wallet_repo.get_by_user_id_for_update.return_value = None

    with pytest.raises(WalletNotFoundError):
        service.withdraw(uuid.uuid4(), "key-1", Decimal("10.00"))


def test_unique_key_race_is_treated_as_replay(service, db, wallet_repo, txn_repo):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    existing = make_existing(wallet, "30.00")
    txn_repo.get_by_user_and_key.side_effect = [None, existing]
    txn_repo.add.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    result = service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    assert result.created is False
    assert result.transaction is existing
    db.rollback.assert_called_once()
    db.commit.assert_not_called()