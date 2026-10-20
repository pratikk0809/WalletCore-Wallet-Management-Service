"""Read-only business logic for account statements. Never imports FastAPI."""
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from app.core.money import TWO_PLACES
from app.core.timeutils import as_utc
from app.exceptions import ValidationError, WalletNotFoundError
from app.models.ledger_entry import LedgerEntry
from app.models.wallet import Wallet
from app.repositories.ledger_repo import LedgerRepository
from app.repositories.wallet_repo import WalletRepository

logger = logging.getLogger(__name__)

DEFAULT_STATEMENT_LIMIT = 500
MAX_STATEMENT_LIMIT = 1000
MAX_PERIOD = timedelta(days=366)


class StatementLine(NamedTuple):
    entry: LedgerEntry
    txn_type: str
    reference: str | None


class Statement(NamedTuple):
    wallet: Wallet
    period_start: datetime
    period_end: datetime
    opening_balance: Decimal
    closing_balance: Decimal
    total_credits: Decimal
    total_debits: Decimal
    entry_count: int
    truncated: bool  # True = more entries exist than `lines` holds
    lines: list[StatementLine]


def _money(value: Decimal | None) -> Decimal:
    """No value means zero; always exactly 2 decimal places."""
    return (value if value is not None else Decimal("0")).quantize(TWO_PLACES)


class StatementService:
    def __init__(
        self, wallet_repo: WalletRepository, ledger_repo: LedgerRepository
    ) -> None:
        self.wallet_repo = wallet_repo
        self.ledger_repo = ledger_repo

    def get_statement(
        self,
        user_id: uuid.UUID,
        period_start: datetime,
        period_end: datetime,
        limit: int = DEFAULT_STATEMENT_LIMIT,
    ) -> Statement:
        """Ledger statement for [period_start, period_end), oldest entry first."""
        # 1. Validate input before touching the database.
        start = as_utc(period_start)
        end = as_utc(period_end)
        if start >= end:
            raise ValidationError("period_start must be before period_end.")
        if end - start > MAX_PERIOD:
            raise ValidationError("The period can be at most 366 days.")

        wallet = self.wallet_repo.get_by_user_id(user_id)
        if wallet is None:
            logger.error("wallet_missing_for_user", extra={"user_id": str(user_id)})
            raise WalletNotFoundError()

        # 2. Balances come from the ledger snapshots, totals from one SQL SUM.
        opening = _money(self.ledger_repo.last_balance_before(wallet.id, start))
        closing = _money(self.ledger_repo.last_balance_before(wallet.id, end))
        totals = self.ledger_repo.totals_between(wallet.id, start, end)
        rows = self.ledger_repo.list_between(wallet.id, start, end, limit)

        credits = _money(totals.credits)
        debits = _money(totals.debits)

        # 3. Reconciliation guard: the ledger must add up.
        if opening + credits - debits != closing:
            logger.error(
                "statement_reconciliation_mismatch",
                extra={
                    "wallet_id": str(wallet.id),
                    "opening": str(opening),
                    "credits": str(credits),
                    "debits": str(debits),
                    "closing": str(closing),
                },
            )

        lines = [StatementLine(*row) for row in rows]
        return Statement(
            wallet=wallet,
            period_start=start,
            period_end=end,
            opening_balance=opening,
            closing_balance=closing,
            total_credits=credits,
            total_debits=debits,
            entry_count=totals.count,
            truncated=totals.count > len(lines),
            lines=lines,
        )