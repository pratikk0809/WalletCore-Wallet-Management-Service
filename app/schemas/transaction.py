"""Request/response shapes for money endpoints."""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DepositRequest(BaseModel):
    # Parsed as Decimal (never float). The service enforces the real rules
    # (positive, limits, 2 decimal places) so they live in one place.
    amount: Decimal
    reference: str | None = Field(default=None, max_length=255)


class WithdrawRequest(BaseModel):
    amount: Decimal
    reference: str | None = Field(default=None, max_length=255)


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    amount: Decimal  # sent as a string, e.g. "100.00"
    currency: str
    source_wallet_id: uuid.UUID | None
    destination_wallet_id: uuid.UUID | None
    reference: str | None
    created_at: datetime