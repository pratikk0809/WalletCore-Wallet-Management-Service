"""Wallet model."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import WalletStatus


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        # The database itself refuses a negative balance.
        CheckConstraint("balance >= 0", name="balance_non_negative"),
        CheckConstraint("status IN ('active', 'frozen')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # unique=True -> one wallet per user in v1 (also gives us a fast lookup index).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # NUMERIC(18,2): exact decimal, never float. Python side uses Decimal.
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default=WalletStatus.ACTIVE.value,
        server_default=WalletStatus.ACTIVE.value,
        nullable=False,
    )

    # For optional optimistic locking later (Phase 4/6).
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )