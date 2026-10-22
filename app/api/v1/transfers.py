"""Transfer endpoint. HTTP only: parse, call the service, return."""

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_current_user, get_transaction_service
from app.api.rate_limits import limit_money
from app.api.responses import REPLAYED, errors
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
    summary="Send money to another wallet",
    dependencies=[Depends(limit_money)],
    responses={
        **REPLAYED,
        **errors(
            401,
            403,
            404,
            409,
            422,
            429,
            custom={
                403: (
                    "Your wallet or the destination wallet is frozen.",
                    "WALLET_FROZEN",
                    "Destination wallet is frozen.",
                ),
                404: (
                    "The destination wallet does not exist.",
                    "WALLET_NOT_FOUND",
                    "Destination wallet not found.",
                ),
                422: (
                    "Invalid amount, transfer to yourself, insufficient funds or daily limit exceeded.",
                    "INSUFFICIENT_FUNDS",
                    "Wallet balance is too low.",
                ),
            },
        ),
    },
)
def create_transfer(
    body: TransferRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> Transaction:
    """Send money from your wallet to another wallet, atomically.

    201 = new transfer, 200 = replay of an earlier request with the same key.
    """
    result = service.transfer(
        user.id,
        idempotency_key,
        body.destination_wallet_id,
        body.amount,
        body.reference,
    )
    return respond(result, response)
