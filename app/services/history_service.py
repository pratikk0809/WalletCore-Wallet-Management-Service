"""Read-only business logic for transaction history. Never imports FastAPI."""
import logging
import uuid
from datetime import datetime
from typing import NamedTuple

from app.core.pagination import decode_cursor, encode_cursor
from app.core.timeutils import as_utc
from app.exceptions import (
    TransactionNotFoundError,
    ValidationError,
    WalletNotFoundError,
)
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repo import TransactionRepository
from app.repositories.wallet_repo import WalletRepository

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class TransactionPage(NamedTuple):
    wallet_id: uuid.UUID
    items: list[Transaction]
    next_cursor: str | None  # None = this was the last page


class TransactionDetail(NamedTuple):
    wallet_id: uuid.UUID
    transaction: Transaction


class HistoryService:
    def __init__(
        self, wallet_repo: WalletRepository, transaction_repo: TransactionRepository
    ) -> None:
        self.wallet_repo = wallet_repo
        self.transaction_repo = transaction_repo

    def list_my_transactions(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
        txn_type: str | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> TransactionPage:
        """One page of the user's transactions, newest first."""
        # 1. Validate input before touching the database.
        #    A date without a timezone is treated as UTC (shared helper).
        created_from = as_utc(created_from) if created_from else None
        created_to = as_utc(created_to) if created_to else None
        if created_from and created_to and created_from > created_to:
            raise ValidationError("created_from must not be after created_to.")
        after = decode_cursor(cursor) if cursor else None

        # 2. Fetch one extra row: if it exists, there is a next page.
        wallet = self._my_wallet(user_id)
        rows = self.transaction_repo.list_for_wallet(
            wallet.id,
            limit + 1,
            txn_type=txn_type,
            status=status,
            created_from=created_from,
            created_to=created_to,
            after=after,
        )
        has_more = len(rows) > limit
        items = rows[:limit]

        # 3. The cursor points at the LAST item we are returning.
        next_cursor = None
        if has_more:
            last = items[-1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return TransactionPage(wallet.id, items, next_cursor)

    def get_my_transaction(
        self, user_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> TransactionDetail:
        """One transaction, only if the user's wallet took part in it."""
        wallet = self._my_wallet(user_id)
        transaction = self.transaction_repo.get_for_wallet(transaction_id, wallet.id)
        if transaction is None:
            raise TransactionNotFoundError()
        return TransactionDetail(wallet.id, transaction)

    def _my_wallet(self, user_id: uuid.UUID) -> Wallet:
        wallet = self.wallet_repo.get_by_user_id(user_id)
        if wallet is None:
            logger.error("wallet_missing_for_user", extra={"user_id": str(user_id)})
            raise WalletNotFoundError()
        return wallet