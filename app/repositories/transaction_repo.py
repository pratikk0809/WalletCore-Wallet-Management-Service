"""Database access for transactions. No business rules here."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.models.enums import TransactionStatus
from app.models.transaction import Transaction


class TransactionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_user_and_key(
        self, user_id: uuid.UUID, idempotency_key: str
    ) -> Transaction | None:
        return self.db.scalar(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.idempotency_key == idempotency_key,
            )
        )

    def sum_outgoing_since(self, wallet_id: uuid.UUID, since: datetime) -> Decimal:
        """Total of completed transactions that took money OUT of this wallet
        (withdrawals and transfers) since `since`. Returns 0 if there are none."""
        total = self.db.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.source_wallet_id == wallet_id,
                Transaction.status == TransactionStatus.COMPLETED.value,
                Transaction.created_at >= since,
            )
        )
        return Decimal(total)

    def list_for_wallet(
        self,
        wallet_id: uuid.UUID,
        limit: int,
        *,
        txn_type: str | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[Transaction]:
        """Transactions where this wallet is the source OR the destination,
        newest first. `after` is a decoded cursor: only rows strictly older
        than that (created_at, id) position are returned."""
        stmt = select(Transaction).where(
            or_(
                Transaction.source_wallet_id == wallet_id,
                Transaction.destination_wallet_id == wallet_id,
            )
        )
        if txn_type is not None:
            stmt = stmt.where(Transaction.type == txn_type)
        if status is not None:
            stmt = stmt.where(Transaction.status == status)
        if created_from is not None:
            stmt = stmt.where(Transaction.created_at >= created_from)
        if created_to is not None:
            stmt = stmt.where(Transaction.created_at <= created_to)
        if after is not None:
            # Compare the (created_at, id) PAIR so equal timestamps are handled.
            stmt = stmt.where(
                tuple_(Transaction.created_at, Transaction.id)
                < tuple_(after[0], after[1])
            )
        stmt = stmt.order_by(
            Transaction.created_at.desc(), Transaction.id.desc()
        ).limit(limit)
        return list(self.db.scalars(stmt))

    def get_for_wallet(
        self, transaction_id: uuid.UUID, wallet_id: uuid.UUID
    ) -> Transaction | None:
        """One transaction, but only if this wallet took part in it."""
        return self.db.scalar(
            select(Transaction).where(
                Transaction.id == transaction_id,
                or_(
                    Transaction.source_wallet_id == wallet_id,
                    Transaction.destination_wallet_id == wallet_id,
                ),
            )
        )

    def add(self, transaction: Transaction) -> Transaction:
        """Stage the transaction and send the INSERT (flush) without committing."""
        self.db.add(transaction)
        self.db.flush()
        return transaction