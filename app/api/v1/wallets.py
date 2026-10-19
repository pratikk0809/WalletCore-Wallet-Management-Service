"""Wallet endpoints. HTTP only."""
from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    get_current_user,
    get_limit_service,
    get_transaction_service,
    get_wallet_service,
)
from app.api.v1.common import IdempotencyKey, respond
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.schemas.transaction import (
    DepositRequest,
    TransactionResponse,
    WithdrawRequest,
)
from app.schemas.wallet import DailyLimitResponse, WalletResponse
from app.services.limit_service import LimitService
from app.services.transaction_service import TransactionService
from app.services.wallet_service import WalletService

router = APIRouter(prefix="/wallets", tags=["wallets"])


@router.get("/me", response_model=WalletResponse)
def get_my_wallet(
    user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
) -> Wallet:
    """The logged-in user's wallet and balance."""
    return service.get_my_wallet(user.id)


@router.get("/me/limits", response_model=DailyLimitResponse)
def get_my_limits(
    user: User = Depends(get_current_user),
    wallet_service: WalletService = Depends(get_wallet_service),
    limit_service: LimitService = Depends(get_limit_service),
) -> DailyLimitResponse:
    """How much you have sent today (UTC) and how much of the daily limit is left."""
    wallet = wallet_service.get_my_wallet(user.id)
    usage = limit_service.get_daily_usage(wallet.id)
    return DailyLimitResponse(
        daily_limit=usage.limit,
        used_today=usage.used,
        remaining_today=usage.remaining,
        resets_at=usage.resets_at,
    )


@router.post("/deposit", response_model=TransactionResponse, status_code=201)
def deposit(
    body: DepositRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> Transaction:
    """Add funds to your wallet. 201 = new deposit, 200 = replay of an earlier request."""
    result = service.deposit(user.id, idempotency_key, body.amount, body.reference)
    return respond(result, response)


@router.post("/withdraw", response_model=TransactionResponse, status_code=201)
def withdraw(
    body: WithdrawRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> Transaction:
    """Withdraw funds. 201 = new, 200 = replay, 422 = insufficient funds or daily limit."""
    result = service.withdraw(user.id, idempotency_key, body.amount, body.reference)
    return respond(result, response)