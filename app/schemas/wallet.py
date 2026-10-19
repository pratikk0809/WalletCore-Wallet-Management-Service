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