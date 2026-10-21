"""Unit tests: how TransactionService uses the daily limit (limit service mocked)."""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.idempotency import compute_request_hash
from app.exceptions import LimitExceededError
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
    repo.get_by_user_and_key.return_value = None
    return repo


@pytest.fixture
def limit_service():
    return MagicMock()


@pytest.fixture
def service(db, wallet_repo, txn_repo, limit_service):
    return TransactionService(db, wallet_repo, txn_repo, MagicMock(), limit_service, MagicMock())


def make_wallet(balance: str = "100.00") -> Wallet:
    return Wallet(
        id=uuid.uuid4(), user_id=uuid.uuid4(), currency="INR",
        balance=Decimal(balance), status="active", version=1,
    )


def test_withdrawal_checks_the_daily_limit_for_this_wallet(service, wallet_repo, limit_service):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    limit_service.check_daily_limit.assert_called_once_with(wallet.id, Decimal("30.00"))


def test_limit_exceeded_stops_the_withdrawal_and_changes_nothing(
    service, db, wallet_repo, limit_service
):
    wallet = make_wallet("100.00")
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    limit_service.check_daily_limit.side_effect = LimitExceededError()

    with pytest.raises(LimitExceededError):
        service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    assert wallet.balance == Decimal("100.00")
    db.commit.assert_not_called()


def test_deposits_are_not_limited(service, wallet_repo, limit_service):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet

    service.deposit(wallet.user_id, "key-1", Decimal("30.00"))

    limit_service.check_daily_limit.assert_not_called()


def test_replay_does_not_check_the_limit(service, wallet_repo, txn_repo, limit_service):
    wallet = make_wallet()
    wallet_repo.get_by_user_id_for_update.return_value = wallet
    txn_repo.get_by_user_and_key.return_value = Transaction(
        id=uuid.uuid4(), user_id=wallet.user_id, type="withdrawal", status="completed",
        amount=Decimal("30.00"), currency="INR", source_wallet_id=wallet.id,
        idempotency_key="key-1",
        request_hash=compute_request_hash("withdrawal", Decimal("30.00"), None),
    )

    result = service.withdraw(wallet.user_id, "key-1", Decimal("30.00"))

    assert result.created is False
    limit_service.check_daily_limit.assert_not_called()