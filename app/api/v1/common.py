"""Pieces shared by the money endpoints (wallets and transfers)."""

from typing import Annotated

from fastapi import Header, Response

from app.models.transaction import Transaction
from app.services.transaction_service import TransactionResult

# Same safe character set as request IDs: stops junk and log injection.
IdempotencyKey = Annotated[
    str,
    Header(
        pattern=r"^[A-Za-z0-9._-]{1,64}$",
        description="Unique key per operation (1-64 chars: letters, digits, . _ -). "
        "Retrying with the same key never repeats the operation.",
    ),
]


def respond(result: TransactionResult, response: Response) -> Transaction:
    """201 = new transaction, 200 + header = replay of an earlier request."""
    if not result.created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return result.transaction
