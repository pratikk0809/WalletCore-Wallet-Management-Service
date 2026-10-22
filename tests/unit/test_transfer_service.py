"""Unit tests for TransactionService.transfer. Repositories and DB are mocked."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.idempotency import compute_request_hash
from app.exceptions import (
    DuplicateTransactionError,
    InsufficientFundsError,
    LimitExceededError,
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
    return MagicMock()


@pytest.fixture
def service(db, wallet_repo, txn_repo, ledger_repo, limit_service):
    return TransactionService(db, wallet_repo, txn_repo, ledger_repo, limit_service, MagicMock())


def make_wallet(balance: str = "100.00", status: str = "active", currency: str = "INR"):
    return Wallet(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        currency=currency,
        balance=Decimal(balance),
        status=status,
        version=1,
    )


@pytest.fixture
def pair(wallet_repo):
    """A sender (100.00) and a recipient (5.00), already wired into the mocked repo."""
    source = make_wallet("100.00")
    destination = make_wallet("5.00")
    wallet_repo.get_by_user_id.return_value = source
    wallet_repo.get_by_ids_for_update.return_value = [source, destination]
    return source, destination


def make_existing(source: Wallet, destination: Wallet, amount: str = "30.00"):
    value = Decimal(amount)
    return Transaction(
        id=uuid.uuid4(),
        user_id=source.user_id,
        type="transfer",
        status="completed",
        amount=value,
        currency="INR",
        source_wallet_id=source.id,
        destination_wallet_id=destination.id,
        idempotency_key="key-1",
        request_hash=compute_request_hash("transfer", value, None, destination=str(destination.id)),
    )


def test_transfer_moves_money_and_bumps_both_versions(service, pair):
    source, destination = pair

    service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    assert source.balance == Decimal("70.00")
    assert destination.balance == Decimal("35.00")
    assert source.version == 2 and destination.version == 2


def test_transfer_writes_balanced_ledger_entries(service, pair, ledger_repo):
    source, destination = pair

    service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    entries = ledger_repo.add_many.call_args.args[0]
    debit = next(e for e in entries if e.direction == "debit")
    credit = next(e for e in entries if e.direction == "credit")
    assert len(entries) == 2
    assert debit.amount == credit.amount == Decimal("30.00")
    assert debit.wallet_id == source.id and debit.balance_after == Decimal("70.00")
    assert credit.wallet_id == destination.id and credit.balance_after == Decimal("35.00")


def test_transfer_commits_once_and_sets_both_wallets(service, db, pair, wallet_repo):
    source, destination = pair

    result = service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"), " rent ")

    assert result.created is True
    assert result.transaction.type == "transfer"
    assert result.transaction.status == "completed"
    assert result.transaction.reference == "rent"
    assert result.transaction.source_wallet_id == source.id
    assert result.transaction.destination_wallet_id == destination.id
    db.commit.assert_called_once()
    # Both rows were requested for locking in one call.
    asked_ids = wallet_repo.get_by_ids_for_update.call_args.args[0]
    assert set(asked_ids) == {source.id, destination.id}


def test_daily_limit_is_checked_for_the_sender_only(service, pair, limit_service):
    source, destination = pair

    service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    limit_service.check_daily_limit.assert_called_once_with(source.id, Decimal("30.00"))


def test_transferring_the_exact_balance_is_allowed(service, pair):
    source, destination = pair

    service.transfer(source.user_id, "key-1", destination.id, Decimal("100.00"))

    assert source.balance == Decimal("0.00")
    assert destination.balance == Decimal("105.00")


def test_insufficient_funds_raises_and_changes_nothing(service, db, pair, ledger_repo):
    source, destination = pair

    with pytest.raises(InsufficientFundsError):
        service.transfer(source.user_id, "key-1", destination.id, Decimal("100.01"))

    assert source.balance == Decimal("100.00") and destination.balance == Decimal("5.00")
    ledger_repo.add_many.assert_not_called()
    db.commit.assert_not_called()


def test_limit_exceeded_changes_nothing(service, db, pair, limit_service):
    source, destination = pair
    limit_service.check_daily_limit.side_effect = LimitExceededError()

    with pytest.raises(LimitExceededError):
        service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    assert source.balance == Decimal("100.00")
    db.commit.assert_not_called()


def test_self_transfer_is_rejected_before_locking(service, wallet_repo):
    source = make_wallet()
    wallet_repo.get_by_user_id.return_value = source

    with pytest.raises(ValidationError, match="own wallet"):
        service.transfer(source.user_id, "key-1", source.id, Decimal("10.00"))

    wallet_repo.get_by_ids_for_update.assert_not_called()


def test_unknown_destination_raises_not_found(service, db, wallet_repo):
    source = make_wallet()
    wallet_repo.get_by_user_id.return_value = source
    wallet_repo.get_by_ids_for_update.return_value = [source]  # destination missing

    with pytest.raises(WalletNotFoundError, match="Destination"):
        service.transfer(source.user_id, "key-1", uuid.uuid4(), Decimal("10.00"))

    db.commit.assert_not_called()


def test_frozen_sender_is_rejected(service, db, pair):
    source, destination = pair
    source.status = "frozen"

    with pytest.raises(WalletFrozenError, match="Your wallet"):
        service.transfer(source.user_id, "key-1", destination.id, Decimal("10.00"))

    db.commit.assert_not_called()


def test_frozen_recipient_is_rejected(service, db, pair):
    source, destination = pair
    destination.status = "frozen"

    with pytest.raises(WalletFrozenError, match="Destination"):
        service.transfer(source.user_id, "key-1", destination.id, Decimal("10.00"))

    assert source.balance == Decimal("100.00")
    db.commit.assert_not_called()


def test_different_currencies_are_rejected(service, db, pair):
    source, destination = pair
    destination.currency = "USD"

    with pytest.raises(ValidationError, match="currency"):
        service.transfer(source.user_id, "key-1", destination.id, Decimal("10.00"))

    db.commit.assert_not_called()


def test_replay_returns_original_without_writing(service, db, pair, txn_repo, ledger_repo):
    source, destination = pair
    existing = make_existing(source, destination)
    txn_repo.get_by_user_and_key.return_value = existing

    result = service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    assert result.created is False
    assert result.transaction is existing
    assert source.balance == Decimal("100.00")  # unchanged
    ledger_repo.add_many.assert_not_called()
    db.commit.assert_not_called()


def test_same_key_with_different_destination_is_a_conflict(
    service, db, pair, txn_repo, wallet_repo
):
    source, destination = pair
    other = make_wallet()
    wallet_repo.get_by_ids_for_update.return_value = [source, other]
    txn_repo.get_by_user_and_key.return_value = make_existing(source, destination)

    with pytest.raises(DuplicateTransactionError):
        service.transfer(source.user_id, "key-1", other.id, Decimal("30.00"))

    db.commit.assert_not_called()


def test_invalid_amount_is_rejected_before_touching_the_database(service, wallet_repo):
    with pytest.raises(ValidationError):
        service.transfer(uuid.uuid4(), "key-1", uuid.uuid4(), Decimal("10.001"))

    wallet_repo.get_by_user_id.assert_not_called()


def test_unique_key_race_is_treated_as_replay(service, db, pair, txn_repo):
    source, destination = pair
    existing = make_existing(source, destination)
    txn_repo.get_by_user_and_key.side_effect = [None, existing]
    txn_repo.add.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    result = service.transfer(source.user_id, "key-1", destination.id, Decimal("30.00"))

    assert result.created is False
    assert result.transaction is existing
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
