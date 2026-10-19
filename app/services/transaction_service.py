"""Business logic for money movement. Never imports FastAPI."""
import logging
import uuid
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.idempotency import compute_request_hash
from app.core.money import validate_amount
from app.exceptions import (
    DuplicateTransactionError,
    InsufficientFundsError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.models.enums import (
    LedgerDirection,
    TransactionStatus,
    TransactionType,
    WalletStatus,
)
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.repositories.ledger_repo import LedgerRepository
from app.repositories.transaction_repo import TransactionRepository
from app.repositories.wallet_repo import WalletRepository
from app.services.limit_service import LimitService

logger = logging.getLogger(__name__)


class TransactionResult(NamedTuple):
    transaction: Transaction
    created: bool  # True = new transaction, False = replay of an earlier request


def _clean_reference(reference: str | None) -> str | None:
    """Trim whitespace; an empty reference becomes None."""
    if reference is None:
        return None
    return reference.strip() or None


class TransactionService:
    def __init__(
        self,
        db: Session,
        wallet_repo: WalletRepository,
        transaction_repo: TransactionRepository,
        ledger_repo: LedgerRepository,
        limit_service: LimitService,
    ) -> None:
        self.db = db
        self.wallet_repo = wallet_repo
        self.transaction_repo = transaction_repo
        self.ledger_repo = ledger_repo
        self.limit_service = limit_service

    def deposit(
        self,
        user_id: uuid.UUID,
        idempotency_key: str,
        raw_amount: object,
        reference: str | None = None,
    ) -> TransactionResult:
        """Add money to the user's wallet. One DB transaction, safe to retry."""
        return self._execute(
            TransactionType.DEPOSIT, user_id, idempotency_key, raw_amount, reference
        )

    def withdraw(
        self,
        user_id: uuid.UUID,
        idempotency_key: str,
        raw_amount: object,
        reference: str | None = None,
    ) -> TransactionResult:
        """Take money out of the user's wallet. Never allows a negative balance."""
        return self._execute(
            TransactionType.WITHDRAWAL, user_id, idempotency_key, raw_amount, reference
        )

    def _execute(
        self,
        txn_type: TransactionType,
        user_id: uuid.UUID,
        idempotency_key: str,
        raw_amount: object,
        reference: str | None,
    ) -> TransactionResult:
        """Shared flow for deposit and withdrawal (money moves between the
        user's wallet and the outside world)."""
        is_deposit = txn_type is TransactionType.DEPOSIT

        # 1. Validate input before touching the database.
        amount = validate_amount(raw_amount)
        reference = _clean_reference(reference)
        request_hash = compute_request_hash(txn_type.value, amount, reference)

        # 2. Lock the wallet row. Concurrent requests for this wallet queue here.
        wallet = self.wallet_repo.get_by_user_id_for_update(user_id)
        if wallet is None:
            logger.error("wallet_missing_for_user", extra={"user_id": str(user_id)})
            raise WalletNotFoundError()

        # 3. Idempotency: have we already handled this key?
        existing = self.transaction_repo.get_by_user_and_key(user_id, idempotency_key)
        if existing is not None:
            return self._replay(existing, request_hash)

        # 4. Business rule: frozen wallets cannot send or receive.
        if wallet.status != WalletStatus.ACTIVE:
            logger.warning(
                "transaction_rejected_wallet_frozen",
                extra={"wallet_id": str(wallet.id), "type": txn_type.value},
            )
            raise WalletFrozenError()

        if not is_deposit:
            # 5. Business rule: a withdrawal needs enough balance.
            #    This runs while the row is locked, so the balance cannot change under us.
            if wallet.balance < amount:
                logger.warning(
                    "withdrawal_rejected_insufficient_funds",
                    extra={"wallet_id": str(wallet.id), "amount": str(amount)},
                )
                raise InsufficientFundsError()

            # 6. Business rule: daily outgoing limit (also under the lock).
            self.limit_service.check_daily_limit(wallet.id, amount)

        # 7. Build the transaction and its two ledger entries.
        new_balance: Decimal = (
            wallet.balance + amount if is_deposit else wallet.balance - amount
        )
        transaction = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            type=txn_type.value,
            status=TransactionStatus.COMPLETED.value,
            amount=amount,
            currency=wallet.currency,
            source_wallet_id=None if is_deposit else wallet.id,
            destination_wallet_id=wallet.id if is_deposit else None,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            reference=reference,
        )
        entries = [
            # Wallet side: deposit = money IN (credit), withdrawal = money OUT (debit).
            LedgerEntry(
                id=uuid.uuid4(),
                transaction_id=transaction.id,
                wallet_id=wallet.id,
                direction=(
                    LedgerDirection.CREDIT if is_deposit else LedgerDirection.DEBIT
                ).value,
                amount=amount,
                balance_after=new_balance,
            ),
            # External side: always the opposite direction, no wallet, no balance.
            LedgerEntry(
                id=uuid.uuid4(),
                transaction_id=transaction.id,
                wallet_id=None,
                direction=(
                    LedgerDirection.DEBIT if is_deposit else LedgerDirection.CREDIT
                ).value,
                amount=amount,
                balance_after=None,
            ),
        ]

        # 8. Save everything together, or nothing.
        try:
            self.transaction_repo.add(transaction)  # must exist before the entries
            self.ledger_repo.add_many(entries)
            wallet.balance = new_balance
            wallet.version += 1
            self.db.commit()
        except IntegrityError:
            # Safety net: the unique constraint caught a duplicate key that
            # slipped past step 3. Treat it as a replay if the original exists.
            self.db.rollback()
            existing = self.transaction_repo.get_by_user_and_key(
                user_id, idempotency_key
            )
            if existing is None:
                raise
            return self._replay(existing, request_hash)

        self.db.refresh(transaction)  # load DB-generated values such as created_at
        logger.info(
            f"{txn_type.value}_completed",
            extra={
                "transaction_id": str(transaction.id),
                "wallet_id": str(wallet.id),
                "amount": str(amount),
            },
        )
        return TransactionResult(transaction, created=True)

    def _replay(self, existing: Transaction, request_hash: str) -> TransactionResult:
        """Same key seen before: return the original, or reject if the body differs."""
        if existing.request_hash != request_hash:
            logger.warning(
                "idempotency_key_conflict",
                extra={"transaction_id": str(existing.id)},
            )
            raise DuplicateTransactionError()
        logger.info("idempotent_replay", extra={"transaction_id": str(existing.id)})
        return TransactionResult(existing, created=False)