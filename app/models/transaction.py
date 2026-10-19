"""Transaction model: one business operation (deposit, withdrawal, transfer)."""
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import TransactionStatus


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "type IN ('deposit', 'withdrawal', 'transfer')", name="type_valid"
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'reversed')",
            name="status_valid",
        ),
        # The database itself enforces which wallets each type needs:
        #   deposit    -> money goes IN to a wallet (no source)
        #   withdrawal -> money goes OUT of a wallet (no destination)
        #   transfer   -> both wallets, and they must differ (no self-transfer)
        CheckConstraint(
            "(type = 'deposit' AND source_wallet_id IS NULL "
            "AND destination_wallet_id IS NOT NULL) OR "
            "(type = 'withdrawal' AND source_wallet_id IS NOT NULL "
            "AND destination_wallet_id IS NULL) OR "
            "(type = 'transfer' AND source_wallet_id IS NOT NULL "
            "AND destination_wallet_id IS NOT NULL "
            "AND source_wallet_id <> destination_wallet_id)",
            name="wallet_shape_valid",
        ),
        # A key can be reused by different users, but never twice by the same user.
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_transactions_user_id_idempotency_key"
        ),
        # Speeds up "history for this wallet, newest first".
        Index("ix_transactions_source_wallet_created", "source_wallet_id", "created_at"),
        Index(
            "ix_transactions_destination_wallet_created",
            "destination_wallet_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Who initiated it (the idempotency key is scoped to this user).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=TransactionStatus.PENDING.value,
        server_default=TransactionStatus.PENDING.value,
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    source_wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=True
    )
    destination_wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=True
    )

    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 (hex = 64 chars) of the request body, to detect "same key, different request".
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )