"""Transfer endpoint. HTTP only: parse, call the service, return."""
from fastapi import APIRouter, Depends, Response

from app.api.deps import get_current_user, get_transaction_service
from app.api.rate_limits import limit_money
from app.api.v1.common import IdempotencyKey, respond
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.transaction import TransactionResponse, TransferRequest
from app.services.transaction_service import TransactionService

router = APIRouter(prefix="/transfers", tags=["transfers"])


# The path is "" (not "/transfer"): together with the router prefix the URL is POST /transfers.
@router.post(
    "",
    response_model=TransactionResponse,
    status_code=201,
    dependencies=[Depends(limit_money)],
)
def create_transfer(
    body: TransferRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> Transaction:
    """Send money from your wallet to another wallet.

    201 = new transfer, 200 = replay, 404 = unknown destination,
    403 = a wallet is frozen, 422 = invalid amount, insufficient funds or daily limit,
    429 = too many requests.
    """
    result = service.transfer(
        user.id,
        idempotency_key,
        body.destination_wallet_id,
        body.amount,
        body.reference,
    )
    return respond(result, response)