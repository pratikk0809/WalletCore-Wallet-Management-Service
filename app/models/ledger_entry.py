"""LedgerEntry model: one line of the double-entry ledger. Append-only."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("direction IN ('debit', 'credit')", name="direction_valid"),
        CheckConstraint("balance_after >= 0", name="balance_after_non_negative"),
        # External side (wallet_id NULL) has no balance; wallet side must have one.
        CheckConstraint(
            "(wallet_id IS NULL) = (balance_after IS NULL)",
            name="external_has_no_balance",
        ),
        # Speeds up statements: "entries for this wallet in a date range".
        Index("ix_ledger_entries_wallet_created", "wallet_id", "created_at"),
        Index("ix_ledger_entries_transaction", "transaction_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False
    )

    # NULL means "outside the system" (cash in for deposits, cash out for withdrawals).
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=True
    )

    # From the wallet's point of view: credit = money in, debit = money out.
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    # Wallet balance right after this entry (NULL for the external side).
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    # clock_timestamp() = the real time of the INSERT (now() would be the time the
    # DB transaction started). Inserts happen under the wallet lock, so for one
    # wallet this order always matches the order of its balance changes.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
