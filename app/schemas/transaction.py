"""Request/response shapes for money endpoints."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

_TRANSACTION_EXAMPLE = {
    "id": "3f2b8c1e-6a4d-4e0b-9c7a-1d2e3f4a5b6c",
    "type": "deposit",
    "status": "completed",
    "amount": "250.75",
    "currency": "INR",
    "source_wallet_id": None,
    "destination_wallet_id": "7c1d9e2a-5b3f-4a8c-8d6e-0f1a2b3c4d5e",
    "reference": "first top-up",
    "created_at": "2026-10-21T10:15:00.123456Z",
}


class DepositRequest(BaseModel):
    # Parsed as Decimal (never float). The service enforces the real rules
    # (positive, limits, 2 decimal places) so they live in one place.
    amount: Decimal = Field(
        examples=["250.75"], description="At most 2 decimals, sent as a string."
    )
    reference: str | None = Field(default=None, max_length=255, examples=["first top-up"])


class WithdrawRequest(BaseModel):
    amount: Decimal = Field(examples=["30.00"], description="At most 2 decimals, sent as a string.")
    reference: str | None = Field(default=None, max_length=255, examples=["rent"])


class TransferRequest(BaseModel):
    # Pydantic checks this is a valid UUID; a malformed ID gives a 422.
    destination_wallet_id: uuid.UUID = Field(
        examples=["7c1d9e2a-5b3f-4a8c-8d6e-0f1a2b3c4d5e"],
        description="The recipient's wallet ID (they can read it from GET /wallets/me).",
    )
    amount: Decimal = Field(examples=["50.00"], description="At most 2 decimals, sent as a string.")
    reference: str | None = Field(default=None, max_length=255, examples=["dinner"])


class TransactionResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, json_schema_extra={"examples": [_TRANSACTION_EXAMPLE]}
    )

    id: uuid.UUID
    type: str
    status: str
    amount: Decimal  # sent as a string, e.g. "100.00"
    currency: str
    source_wallet_id: uuid.UUID | None
    destination_wallet_id: uuid.UUID | None
    reference: str | None
    created_at: datetime


class TransactionHistoryItem(TransactionResponse):
    """A transaction as seen by ONE user."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"examples": [{**_TRANSACTION_EXAMPLE, "direction": "in"}]},
    )

    direction: str  # "in" = money arrived in my wallet, "out" = money left it


class TransactionListResponse(BaseModel):
    items: list[TransactionHistoryItem]
    # Send this back as ?cursor=... to get the next page. null = no more pages.
    next_cursor: str | None
