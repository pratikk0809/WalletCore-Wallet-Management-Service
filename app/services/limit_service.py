"""Daily outgoing-limit rules. Never imports FastAPI."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple

from app.core.config import get_settings
from app.core.money import TWO_PLACES
from app.exceptions import LimitExceededError
from app.repositories.transaction_repo import TransactionRepository

logger = logging.getLogger(__name__)


class DailyUsage(NamedTuple):
    limit: Decimal
    used: Decimal
    remaining: Decimal
    resets_at: datetime  # next 00:00 UTC


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class LimitService:
    def __init__(
        self,
        transaction_repo: TransactionRepository,
        daily_limit: Decimal | None = None,
    ) -> None:
        self.transaction_repo = transaction_repo
        self.daily_limit = (
            get_settings().daily_limit if daily_limit is None else daily_limit
        )

    def get_daily_usage(
        self, wallet_id: uuid.UUID, now: datetime | None = None
    ) -> DailyUsage:
        """How much this wallet has sent today (UTC) and how much is left."""
        now = now or datetime.now(timezone.utc)
        day_start = _start_of_day(now)

        used = self.transaction_repo.sum_outgoing_since(wallet_id, day_start)
        used = used.quantize(TWO_PLACES)
        limit = self.daily_limit.quantize(TWO_PLACES)
        remaining = max(limit - used, Decimal("0.00"))

        return DailyUsage(limit, used, remaining, day_start + timedelta(days=1))

    def check_daily_limit(
        self, wallet_id: uuid.UUID, amount: Decimal, now: datetime | None = None
    ) -> None:
        """Raise LimitExceededError if sending `amount` would pass today's limit.

        Callers must hold the wallet's row lock, so no other request can
        change the total between this check and the commit.
        """
        usage = self.get_daily_usage(wallet_id, now)
        if amount > usage.remaining:
            logger.warning(
                "daily_limit_exceeded",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": str(amount),
                    "used": str(usage.used),
                    "limit": str(usage.limit),
                },
            )
            raise LimitExceededError(
                f"Daily limit of {usage.limit} exceeded. "
                f"Remaining today: {usage.remaining}."
            )