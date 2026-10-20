"""Database access for ledger entries. Append-only: there is no update or delete."""
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import case, func, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models.enums import LedgerDirection
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction


class PeriodTotals(NamedTuple):
    credits: Decimal  # total money in during the period
    debits: Decimal   # total money out during the period
    count: int        # number of entries in the period


class LedgerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_many(self, entries: Sequence[LedgerEntry]) -> None:
        """Stage several entries at once (both sides of a transaction)."""
        self.db.add_all(entries)
        self.db.flush()

    def last_balance_before(
        self, wallet_id: uuid.UUID, before: datetime
    ) -> Decimal | None:
        """The wallet balance right after its latest entry older than `before`.
        None if the wallet has no entries before that moment."""
        return self.db.scalar(
            select(LedgerEntry.balance_after)
            .where(LedgerEntry.wallet_id == wallet_id, LedgerEntry.created_at < before)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .limit(1)
        )

    def totals_between(
        self, wallet_id: uuid.UUID, start: datetime, end: datetime
    ) -> PeriodTotals:
        """Credits, debits and entry count for entries in [start, end)."""
        credits = func.coalesce(
            func.sum(
                case(
                    (LedgerEntry.direction == LedgerDirection.CREDIT.value, LedgerEntry.amount),
                    else_=0,
                )
            ),
            0,
        )
        debits = func.coalesce(
            func.sum(
                case(
                    (LedgerEntry.direction == LedgerDirection.DEBIT.value, LedgerEntry.amount),
                    else_=0,
                )
            ),
            0,
        )
        row = self.db.execute(
            select(credits, debits, func.count(LedgerEntry.id)).where(
                LedgerEntry.wallet_id == wallet_id,
                LedgerEntry.created_at >= start,
                LedgerEntry.created_at < end,
            )
        ).one()
        return PeriodTotals(Decimal(row[0]), Decimal(row[1]), int(row[2]))

    def list_between(
        self, wallet_id: uuid.UUID, start: datetime, end: datetime, limit: int
    ) -> list[Row]:
        """Entries in [start, end), oldest first. Each row is
        (LedgerEntry, transaction type, transaction reference)."""
        stmt = (
            select(LedgerEntry, Transaction.type, Transaction.reference)
            .join(Transaction, Transaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerEntry.wallet_id == wallet_id,
                LedgerEntry.created_at >= start,
                LedgerEntry.created_at < end,
            )
            .order_by(LedgerEntry.created_at.asc(), LedgerEntry.id.asc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).all())