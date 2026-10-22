"""Unit tests for HistoryService (mocked repositories)."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.pagination import decode_cursor, encode_cursor
from app.exceptions import (
    TransactionNotFoundError,
    ValidationError,
    WalletNotFoundError,
)
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.services.history_service import HistoryService

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def wallet():
    return Wallet(id=uuid.uuid4(), user_id=uuid.uuid4(), currency="INR")


@pytest.fixture
def wallet_repo(wallet):
    repo = MagicMock()
    repo.get_by_user_id.return_value = wallet
    return repo


@pytest.fixture
def txn_repo():
    return MagicMock()


@pytest.fixture
def service(wallet_repo, txn_repo):
    return HistoryService(wallet_repo, txn_repo)


def make_txn(wallet: Wallet, minutes_ago: int) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=wallet.user_id,
        type="deposit",
        status="completed",
        amount=Decimal("1.00"),
        currency="INR",
        destination_wallet_id=wallet.id,
        idempotency_key=f"key-{minutes_ago}",
        request_hash="h" * 64,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


def test_more_rows_than_the_limit_gives_a_trimmed_page_and_a_cursor(service, wallet, txn_repo):
    rows = [make_txn(wallet, m) for m in (1, 2, 3)]
    txn_repo.list_for_wallet.return_value = rows

    page = service.list_my_transactions(wallet.user_id, limit=2)

    assert page.items == rows[:2]
    assert page.next_cursor is not None
    assert txn_repo.list_for_wallet.call_args.args[1] == 3  # asked for limit + 1


def test_last_page_has_no_cursor(service, wallet, txn_repo):
    rows = [make_txn(wallet, m) for m in (1, 2)]
    txn_repo.list_for_wallet.return_value = rows

    page = service.list_my_transactions(wallet.user_id, limit=2)

    assert page.items == rows
    assert page.next_cursor is None


def test_cursor_points_at_the_last_returned_item(service, wallet, txn_repo):
    rows = [make_txn(wallet, m) for m in (1, 2, 3)]
    txn_repo.list_for_wallet.return_value = rows

    page = service.list_my_transactions(wallet.user_id, limit=2)

    assert decode_cursor(page.next_cursor) == (rows[1].created_at, rows[1].id)


def test_decoded_cursor_is_passed_to_the_repository(service, wallet, txn_repo):
    txn_repo.list_for_wallet.return_value = []
    position = (NOW, uuid.uuid4())

    service.list_my_transactions(wallet.user_id, cursor=encode_cursor(*position))

    assert txn_repo.list_for_wallet.call_args.kwargs["after"] == position


def test_invalid_cursor_is_rejected_before_any_database_access(
    service, wallet, wallet_repo, txn_repo
):
    with pytest.raises(ValidationError):
        service.list_my_transactions(wallet.user_id, cursor="garbage")

    wallet_repo.get_by_user_id.assert_not_called()
    txn_repo.list_for_wallet.assert_not_called()


def test_from_after_to_is_rejected(service, wallet, txn_repo):
    with pytest.raises(ValidationError, match="created_from"):
        service.list_my_transactions(
            wallet.user_id, created_from=NOW, created_to=NOW - timedelta(days=1)
        )

    txn_repo.list_for_wallet.assert_not_called()


def test_dates_without_a_timezone_are_treated_as_utc(service, wallet, txn_repo):
    txn_repo.list_for_wallet.return_value = []

    service.list_my_transactions(
        wallet.user_id,
        created_from=datetime(2026, 1, 1),
        created_to=datetime(2026, 2, 1),
    )

    kwargs = txn_repo.list_for_wallet.call_args.kwargs
    assert kwargs["created_from"].tzinfo == timezone.utc
    assert kwargs["created_to"].tzinfo == timezone.utc


def test_missing_wallet_raises(service, wallet_repo):
    wallet_repo.get_by_user_id.return_value = None

    with pytest.raises(WalletNotFoundError):
        service.list_my_transactions(uuid.uuid4())


def test_get_transaction_returns_it_together_with_the_wallet_id(service, wallet, txn_repo):
    txn = make_txn(wallet, 1)
    txn_repo.get_for_wallet.return_value = txn

    detail = service.get_my_transaction(wallet.user_id, txn.id)

    assert detail.transaction is txn
    assert detail.wallet_id == wallet.id
    txn_repo.get_for_wallet.assert_called_once_with(txn.id, wallet.id)


def test_get_unknown_or_foreign_transaction_raises(service, wallet, txn_repo):
    txn_repo.get_for_wallet.return_value = None

    with pytest.raises(TransactionNotFoundError):
        service.get_my_transaction(wallet.user_id, uuid.uuid4())
