"""Wallet endpoints. HTTP only."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    get_current_user,
    get_limit_service,
    get_statement_service,
    get_transaction_service,
    get_wallet_service,
)
from app.api.rate_limits import limit_money
from app.api.responses import REPLAYED, errors
from app.api.v1.common import IdempotencyKey, respond
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.schemas.transaction import (
    DepositRequest,
    TransactionResponse,
    WithdrawRequest,
)
from app.schemas.wallet import (
    DailyLimitResponse,
    StatementEntry,
    StatementResponse,
    WalletResponse,
)
from app.services.limit_service import LimitService
from app.services.statement_service import (
    DEFAULT_STATEMENT_LIMIT,
    MAX_STATEMENT_LIMIT,
    StatementService,
)
from app.services.transaction_service import TransactionService
from app.services.wallet_service import WalletService

router = APIRouter(prefix="/wallets", tags=["wallets"])


@router.get(
    "/me", response_model=WalletResponse, summary="My wallet and balance", responses=errors(401)
)
def get_my_wallet(
    user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
) -> Wallet:
    """The logged-in user's wallet and balance."""
    return service.get_my_wallet(user.id)


@router.get(
    "/me/limits",
    response_model=DailyLimitResponse,
    summary="My daily limit usage",
    responses=errors(401),
)
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


@router.get(
    "/me/statement",
    response_model=StatementResponse,
    summary="My account statement",
    responses=errors(401, 422),
)
def get_my_statement(
    period_start: datetime = Query(
        ..., description="Inclusive. ISO 8601, e.g. 2026-10-01T00:00:00Z"
    ),
    period_end: datetime = Query(
        ..., description="Exclusive. ISO 8601. At most 366 days after period_start."
    ),
    limit: int = Query(DEFAULT_STATEMENT_LIMIT, ge=1, le=MAX_STATEMENT_LIMIT),
    user: User = Depends(get_current_user),
    service: StatementService = Depends(get_statement_service),
) -> StatementResponse:
    """Ledger statement for a period: opening balance, every line with its running
    balance (oldest first), closing balance and totals."""
    statement = service.get_statement(user.id, period_start, period_end, limit)
    return StatementResponse(
        wallet_id=statement.wallet.id,
        currency=statement.wallet.currency,
        period_start=statement.period_start,
        period_end=statement.period_end,
        opening_balance=statement.opening_balance,
        closing_balance=statement.closing_balance,
        total_credits=statement.total_credits,
        total_debits=statement.total_debits,
        entry_count=statement.entry_count,
        truncated=statement.truncated,
        entries=[
            StatementEntry(
                id=line.entry.id,
                transaction_id=line.entry.transaction_id,
                type=line.txn_type,
                direction=line.entry.direction,
                amount=line.entry.amount,
                balance_after=line.entry.balance_after,
                reference=line.reference,
                created_at=line.entry.created_at,
            )
            for line in statement.lines
        ],
    )


@router.post(
    "/deposit",
    response_model=TransactionResponse,
    status_code=201,
    dependencies=[Depends(limit_money)],
    responses={
        **REPLAYED,
        **errors(
            401,
            403,
            409,
            422,
            429,
            custom={403: ("Your wallet is frozen.", "WALLET_FROZEN", "Wallet is frozen.")},
        ),
    },
)
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


@router.post(
    "/withdraw",
    response_model=TransactionResponse,
    status_code=201,
    summary="Withdraw money",
    dependencies=[Depends(limit_money)],
    responses={
        **REPLAYED,
        **errors(
            401,
            403,
            409,
            422,
            429,
            custom={
                403: ("Your wallet is frozen.", "WALLET_FROZEN", "Wallet is frozen."),
                422: (
                    "Invalid amount, insufficient funds or daily limit exceeded.",
                    "INSUFFICIENT_FUNDS",
                    "Wallet balance is too low.",
                ),
            },
        ),
    },
)
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
