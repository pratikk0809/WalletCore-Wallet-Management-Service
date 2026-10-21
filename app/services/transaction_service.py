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
    ValidationError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.models.enums import (
    AuditAction,
    LedgerDirection,
    TransactionStatus,
    TransactionType,
    WalletStatus,
)
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.ledger_repo import LedgerRepository
from app.repositories.transaction_repo import TransactionRepository
from app.repositories.wallet_repo import WalletRepository
from app.services.audit_service import AuditService
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
        audit_service: AuditService,
    ) -> None:
        self.db = db
        self.wallet_repo = wallet_repo
        self.transaction_repo = transaction_repo
        self.ledger_repo = ledger_repo
        self.limit_service = limit_service
        self.audit_service = audit_service

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

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

    def transfer(
        self,
        user_id: uuid.UUID,
        idempotency_key: str,
        destination_wallet_id: uuid.UUID,
        raw_amount: object,
        reference: str | None = None,
    ) -> TransactionResult:
        """Move money from the user's wallet to another wallet, atomically."""
        # 1. Validate input before touching the database.
        amount = validate_amount(raw_amount)
        reference = _clean_reference(reference)
        request_hash = compute_request_hash(
            TransactionType.TRANSFER.value,
            amount,
            reference,
            destination=str(destination_wallet_id),
        )

        # 2. Find the sender's wallet. A plain read is safe here: the wallet ID
        #    of a user never changes (one wallet per user).
        source = self.wallet_repo.get_by_user_id(user_id)
        if source is None:
            logger.error("wallet_missing_for_user", extra={"user_id": str(user_id)})
            raise WalletNotFoundError()

        # 3. No self-transfer (the DB has a CHECK for this too).
        if source.id == destination_wallet_id:
            logger.warning(
                "transfer_rejected_self_transfer",
                extra={"wallet_id": str(source.id)},
            )
            raise ValidationError("You cannot transfer to your own wallet.")

        # 4. Lock BOTH wallet rows, in ascending ID order (done by the repository).
        locked = {
            w.id: w
            for w in self.wallet_repo.get_by_ids_for_update(
                [source.id, destination_wallet_id]
            )
        }
        source = locked.get(source.id)
        destination = locked.get(destination_wallet_id)
        if source is None:
            raise WalletNotFoundError()

        # 5. Idempotency: have we already handled this key?
        existing = self.transaction_repo.get_by_user_and_key(user_id, idempotency_key)
        if existing is not None:
            return self._replay(existing, request_hash)

        # 6. Business rules.
        if destination is None:
            logger.warning(
                "transfer_rejected_unknown_destination",
                extra={"wallet_id": str(source.id)},
            )
            raise WalletNotFoundError("Destination wallet not found.")

        if source.status != WalletStatus.ACTIVE:
            logger.warning(
                "transaction_rejected_wallet_frozen",
                extra={"wallet_id": str(source.id), "type": "transfer"},
            )
            raise WalletFrozenError("Your wallet is frozen.")
        if destination.status != WalletStatus.ACTIVE:
            logger.warning(
                "transaction_rejected_wallet_frozen",
                extra={"wallet_id": str(destination.id), "type": "transfer"},
            )
            raise WalletFrozenError("Destination wallet is frozen.")

        if source.currency != destination.currency:
            raise ValidationError("Both wallets must use the same currency.")

        if source.balance < amount:
            logger.warning(
                "transfer_rejected_insufficient_funds",
                extra={"wallet_id": str(source.id), "amount": str(amount)},
            )
            raise InsufficientFundsError()

        # The daily limit applies to the SENDER only (also under the lock).
        self.limit_service.check_daily_limit(source.id, amount)

        # 7. Build the transaction and its two ledger entries.
        source_new: Decimal = source.balance - amount
        destination_new: Decimal = destination.balance + amount
        transaction = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            type=TransactionType.TRANSFER.value,
            status=TransactionStatus.COMPLETED.value,
            amount=amount,
            currency=source.currency,
            source_wallet_id=source.id,
            destination_wallet_id=destination.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            reference=reference,
        )
        entries = [
            LedgerEntry(  # money leaves the sender
                id=uuid.uuid4(),
                transaction_id=transaction.id,
                wallet_id=source.id,
                direction=LedgerDirection.DEBIT.value,
                amount=amount,
                balance_after=source_new,
            ),
            LedgerEntry(  # money arrives at the recipient
                id=uuid.uuid4(),
                transaction_id=transaction.id,
                wallet_id=destination.id,
                direction=LedgerDirection.CREDIT.value,
                amount=amount,
                balance_after=destination_new,
            ),
        ]

        # 8. Save everything together, or nothing.
        result = self._commit(
            transaction,
            entries,
            [(source, source_new), (destination, destination_new)],
            user_id,
            idempotency_key,
            request_hash,
        )
        if result.created:
            logger.info(
                "transfer_completed",
                extra={
                    "transaction_id": str(transaction.id),
                    "source_wallet_id": str(source.id),
                    "destination_wallet_id": str(destination.id),
                    "amount": str(amount),
                },
            )
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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
        result = self._commit(
            transaction,
            entries,
            [(wallet, new_balance)],
            user_id,
            idempotency_key,
            request_hash,
        )
        if result.created:
            logger.info(
                f"{txn_type.value}_completed",
                extra={
                    "transaction_id": str(transaction.id),
                    "wallet_id": str(wallet.id),
                    "amount": str(amount),
                },
            )
        return result

    def _commit(
        self,
        transaction: Transaction,
        entries: list[LedgerEntry],
        balance_changes: list[tuple[Wallet, Decimal]],
        user_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> TransactionResult:
        """Write the transaction, ledger entries, new balances AND the audit row
        in ONE DB transaction. Shared by deposit, withdrawal and transfer."""
        try:
            self.transaction_repo.add(transaction)  # must exist before the entries
            self.ledger_repo.add_many(entries)
            for wallet, new_balance in balance_changes:
                wallet.balance = new_balance
                wallet.version += 1
            # "deposit" -> deposit_completed, "withdrawal" -> withdrawal_completed, ...
            # The free-text `reference` is deliberately NOT copied (it may hold personal data).
            self.audit_service.record(
                AuditAction(f"{transaction.type}_completed"),
                entity_type="transaction",
                entity_id=transaction.id,
                actor_id=user_id,
                metadata={
                    "type": transaction.type,
                    "amount": transaction.amount,
                    "currency": transaction.currency,
                    "source_wallet_id": transaction.source_wallet_id,
                    "destination_wallet_id": transaction.destination_wallet_id,
                },
            )
            self.db.commit()
        except IntegrityError:
            # Safety net: the unique constraint caught a duplicate key that
            # slipped past the idempotency check. Treat it as a replay if the
            # original exists.
            self.db.rollback()
            existing = self.transaction_repo.get_by_user_and_key(
                user_id, idempotency_key
            )
            if existing is None:
                raise
            return self._replay(existing, request_hash)

        self.db.refresh(transaction)  # load DB-generated values such as created_at
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