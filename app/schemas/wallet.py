"""Response shapes for wallet endpoints."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    currency: str
    # Decimal is sent as a JSON string (e.g. "0.00"), so clients never
    # lose precision to floating point.
    balance: Decimal
    status: str
    created_at: datetime
    updated_at: datetime


class DailyLimitResponse(BaseModel):
    daily_limit: Decimal
    used_today: Decimal
    remaining_today: Decimal
    resets_at: datetime  # next 00:00 UTC


class StatementEntry(BaseModel):
    """One ledger line of your wallet."""

    id: uuid.UUID
    transaction_id: uuid.UUID
    type: str  # deposit / withdrawal / transfer
    direction: str  # "credit" = money in, "debit" = money out
    amount: Decimal
    balance_after: Decimal  # running balance right after this line
    reference: str | None
    created_at: datetime


class StatementResponse(BaseModel):
    wallet_id: uuid.UUID
    currency: str
    period_start: datetime  # inclusive
    period_end: datetime  # exclusive
    opening_balance: Decimal
    closing_balance: Decimal
    total_credits: Decimal
    total_debits: Decimal
    entry_count: int  # entries in the whole period
    truncated: bool  # true = `entries` was cut off at `limit`; narrow the period
    entries: list[StatementEntry]
