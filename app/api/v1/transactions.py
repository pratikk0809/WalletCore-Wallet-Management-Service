"""Transaction history endpoints. HTTP only."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from app.api.responses import errors

from app.api.deps import get_current_user, get_history_service
from app.models.enums import TransactionStatus, TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.transaction import (
    TransactionHistoryItem,
    TransactionListResponse,
    TransactionResponse,
)
from app.services.history_service import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, HistoryService

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _to_item(transaction: Transaction, wallet_id: uuid.UUID) -> TransactionHistoryItem:
    """Add the caller's point of view: did money come IN or go OUT of their wallet?"""
    base = TransactionResponse.model_validate(transaction).model_dump()
    direction = "in" if transaction.destination_wallet_id == wallet_id else "out"
    return TransactionHistoryItem(**base, direction=direction)


@router.get("", response_model=TransactionListResponse, summary="List my transactions",
            responses=errors(401, 422))
def list_transactions(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(
        None, max_length=200, description="`next_cursor` from the previous page."
    ),
    txn_type: TransactionType | None = Query(None, alias="type"),
    status: TransactionStatus | None = Query(None),
    created_from: datetime | None = Query(
        None, description="Inclusive. ISO 8601, e.g. 2026-10-01T00:00:00Z"
    ),
    created_to: datetime | None = Query(None, description="Inclusive. ISO 8601."),
    user: User = Depends(get_current_user),
    service: HistoryService = Depends(get_history_service),
) -> TransactionListResponse:
    """Your transactions, newest first. Follow `next_cursor` for older ones."""
    page = service.list_my_transactions(
        user.id,
        limit=limit,
        cursor=cursor,
        txn_type=txn_type.value if txn_type else None,
        status=status.value if status else None,
        created_from=created_from,
        created_to=created_to,
    )
    return TransactionListResponse(
        items=[_to_item(t, page.wallet_id) for t in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{transaction_id}",
    response_model=TransactionHistoryItem,
    summary="Get one transaction",
    responses=errors(
        401, 404, 422,
        custom={404: ("Unknown transaction, or one you took no part in.", "TRANSACTION_NOT_FOUND", "Transaction not found.")},
    ),
)
def get_transaction(
    transaction_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: HistoryService = Depends(get_history_service),
) -> TransactionHistoryItem:
    """One transaction you took part in. 404 if it doesn't exist or isn't yours."""
    detail = service.get_my_transaction(user.id, transaction_id)
    return _to_item(detail.transaction, detail.wallet_id)