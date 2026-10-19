"""Unit tests for LimitService (mocked repository, fixed clock)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.exceptions import LimitExceededError
from app.services.limit_service import LimitService

NOW = datetime(2026, 10, 17, 15, 30, tzinfo=timezone.utc)
DAY_START = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
WALLET_ID = uuid.uuid4()


@pytest.fixture
def repo():
    repo = MagicMock()
    repo.sum_outgoing_since.return_value = Decimal("0")
    return repo


def make_service(repo, limit: str = "100.00") -> LimitService:
    return LimitService(repo, daily_limit=Decimal(limit))


def test_usage_reports_used_and_remaining(repo):
    repo.sum_outgoing_since.return_value = Decimal("60.00")

    usage = make_service(repo).get_daily_usage(WALLET_ID, NOW)

    assert usage.limit == Decimal("100.00")
    assert usage.used == Decimal("60.00")
    assert usage.remaining == Decimal("40.00")


def test_usage_with_no_transactions_is_formatted_with_two_places(repo):
    usage = make_service(repo).get_daily_usage(WALLET_ID, NOW)

    assert str(usage.used) == "0.00"
    assert str(usage.remaining) == "100.00"


def test_remaining_never_goes_below_zero(repo):
    repo.sum_outgoing_since.return_value = Decimal("130.00")  # e.g. limit was lowered

    usage = make_service(repo).get_daily_usage(WALLET_ID, NOW)

    assert usage.remaining == Decimal("0.00")


def test_usage_counts_from_start_of_utc_day_and_reports_reset_time(repo):
    usage = make_service(repo).get_daily_usage(WALLET_ID, NOW)

    repo.sum_outgoing_since.assert_called_once_with(WALLET_ID, DAY_START)
    assert usage.resets_at == datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)


def test_amount_under_the_limit_is_allowed(repo):
    repo.sum_outgoing_since.return_value = Decimal("60.00")
    make_service(repo).check_daily_limit(WALLET_ID, Decimal("39.99"), NOW)


def test_amount_that_exactly_reaches_the_limit_is_allowed(repo):
    repo.sum_outgoing_since.return_value = Decimal("60.00")
    make_service(repo).check_daily_limit(WALLET_ID, Decimal("40.00"), NOW)


def test_amount_over_the_limit_is_rejected(repo):
    repo.sum_outgoing_since.return_value = Decimal("60.00")

    with pytest.raises(LimitExceededError, match="Remaining today: 40.00"):
        make_service(repo).check_daily_limit(WALLET_ID, Decimal("40.01"), NOW)


def test_anything_is_rejected_once_the_limit_is_used_up(repo):
    repo.sum_outgoing_since.return_value = Decimal("100.00")

    with pytest.raises(LimitExceededError):
        make_service(repo).check_daily_limit(WALLET_ID, Decimal("0.01"), NOW)