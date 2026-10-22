"""Request/response shapes for admin endpoints."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, StrictBool


class WalletFreezeRequest(BaseModel):
    # StrictBool: only a real JSON true/false is accepted ("yes" or 1 -> 422).
    frozen: StrictBool = Field(examples=[True], description="true = freeze, false = unfreeze.")
    reason: str | None = Field(
        default=None,
        max_length=255,
        examples=["suspected fraud"],
        description="Free text for the audit log. Keep it free of personal data.",
    )


class AdminWalletResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    currency: str
    balance: Decimal  # sent as a string, e.g. "100.00"
    status: str
    updated_at: datetime
    changed: bool  # false = the wallet already had this status (nothing was done)
