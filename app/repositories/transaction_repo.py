"""Database access for transactions. No business rules here."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
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

    def add(self, transaction: Transaction) -> Transaction:
        """Stage the transaction and send the INSERT (flush) without committing."""
        self.db.add(transaction)
        self.db.flush()
        return transaction