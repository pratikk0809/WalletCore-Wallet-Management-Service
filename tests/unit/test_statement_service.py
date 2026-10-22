"""Unit tests for StatementService (mocked repositories, fixed dates)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, call

import pytest

from app.exceptions import ValidationError, WalletNotFoundError
from app.models.ledger_entry import LedgerEntry
from app.models.wallet import Wallet
from app.repositories.ledger_repo import PeriodTotals
from app.services.statement_service import StatementService

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture
def wallet():
    return Wallet(id=uuid.uuid4(), user_id=uuid.uuid4(), currency="INR")


@pytest.fixture
def wallet_repo(wallet):
    repo = MagicMock()
    repo.get_by_user_id.return_value = wallet
    return repo


def make_ledger_repo(opening, closing, totals, rows=()):
    """A mocked LedgerRepository: opening/closing are looked up by date."""
    repo = MagicMock()
    repo.last_balance_before.side_effect = lambda wallet_id, before: {
        START: opening,
        END: closing,
    }.get(before)
    repo.totals_between.return_value = totals
    repo.list_between.return_value = list(rows)
    return repo


def make_row(wallet, direction, amount, balance_after, txn_type="deposit", reference=None):
    entry = LedgerEntry(
        id=uuid.uuid4(),
        transaction_id=uuid.uuid4(),
        wallet_id=wallet.id,
        direction=direction,
        amount=Decimal(amount),
        balance_after=Decimal(balance_after),
        created_at=START,
    )
    return (entry, txn_type, reference)


def test_statement_reports_balances_totals_and_lines(wallet_repo, wallet):
    rows = [
        make_row(wallet, "credit", "70.00", "170.00", "deposit", "salary"),
        make_row(wallet, "debit", "20.00", "150.00", "withdrawal"),
    ]
    ledger = make_ledger_repo(
        Decimal("100.00"),
        Decimal("150.00"),
        PeriodTotals(Decimal("70.00"), Decimal("20.00"), 2),
        rows,
    )

    statement = StatementService(wallet_repo, ledger).get_statement(wallet.user_id, START, END)

    assert statement.opening_balance == Decimal("100.00")
    assert statement.closing_balance == Decimal("150.00")
    assert statement.total_credits == Decimal("70.00")
    assert statement.total_debits == Decimal("20.00")
    assert statement.entry_count == 2 and statement.truncated is False
    assert [l.txn_type for l in statement.lines] == ["deposit", "withdrawal"]
    assert statement.lines[0].reference == "salary"


def test_wallet_without_any_entries_is_all_zeros(wallet_repo, wallet):
    ledger = make_ledger_repo(None, None, PeriodTotals(Decimal("0"), Decimal("0"), 0))

    statement = StatementService(wallet_repo, ledger).get_statement(wallet.user_id, START, END)

    assert str(statement.opening_balance) == "0.00"
    assert str(statement.closing_balance) == "0.00"
    assert str(statement.total_credits) == "0.00"
    assert statement.lines == [] and statement.truncated is False


def test_first_ever_entry_inside_the_period_means_opening_zero(wallet_repo, wallet):
    ledger = make_ledger_repo(
        None,
        Decimal("30.00"),
        PeriodTotals(Decimal("30.00"), Decimal("0"), 1),
        [make_row(wallet, "credit", "30.00", "30.00")],
    )

    statement = StatementService(wallet_repo, ledger).get_statement(wallet.user_id, START, END)

    assert str(statement.opening_balance) == "0.00"
    assert statement.closing_balance == Decimal("30.00")


def test_more_entries_than_the_limit_sets_truncated_but_totals_cover_everything(
    wallet_repo, wallet
):
    ledger = make_ledger_repo(
        None,
        Decimal("30.00"),
        PeriodTotals(Decimal("30.00"), Decimal("0"), 3),
        [
            make_row(wallet, "credit", "10.00", "10.00"),
            make_row(wallet, "credit", "10.00", "20.00"),
        ],
    )

    statement = StatementService(wallet_repo, ledger).get_statement(
        wallet.user_id, START, END, limit=2
    )

    assert statement.truncated is True
    assert statement.entry_count == 3 and len(statement.lines) == 2
    assert statement.total_credits == Decimal("30.00")


@pytest.mark.parametrize("start, end", [(END, START), (START, START)])
def test_period_start_must_be_before_period_end(wallet_repo, wallet, start, end):
    ledger = make_ledger_repo(None, None, PeriodTotals(Decimal("0"), Decimal("0"), 0))

    with pytest.raises(ValidationError, match="before"):
        StatementService(wallet_repo, ledger).get_statement(wallet.user_id, start, end)

    wallet_repo.get_by_user_id.assert_not_called()
    ledger.totals_between.assert_not_called()


def test_period_is_limited_to_366_days(wallet_repo, wallet):
    ledger = make_ledger_repo(None, None, PeriodTotals(Decimal("0"), Decimal("0"), 0))
    service = StatementService(wallet_repo, ledger)

    with pytest.raises(ValidationError, match="366"):
        service.get_statement(wallet.user_id, START, START + timedelta(days=367))

    # Exactly 366 days is still allowed.
    service.get_statement(wallet.user_id, START, START + timedelta(days=366))


def test_dates_without_a_timezone_are_treated_as_utc(wallet_repo, wallet):
    ledger = make_ledger_repo(None, None, PeriodTotals(Decimal("0"), Decimal("0"), 0))

    StatementService(wallet_repo, ledger).get_statement(
        wallet.user_id, datetime(2026, 10, 1), datetime(2026, 11, 1)
    )

    assert ledger.last_balance_before.call_args_list == [
        call(wallet.id, START),  # opening: balance before the period starts
        call(wallet.id, END),  # closing: balance before the period ends
    ]


def test_period_and_limit_are_passed_to_the_repository(wallet_repo, wallet):
    ledger = make_ledger_repo(None, None, PeriodTotals(Decimal("0"), Decimal("0"), 0))

    StatementService(wallet_repo, ledger).get_statement(wallet.user_id, START, END, limit=50)

    ledger.totals_between.assert_called_once_with(wallet.id, START, END)
    ledger.list_between.assert_called_once_with(wallet.id, START, END, 50)


def test_missing_wallet_raises(wallet_repo):
    wallet_repo.get_by_user_id.return_value = None
    ledger = make_ledger_repo(None, None, PeriodTotals(Decimal("0"), Decimal("0"), 0))

    with pytest.raises(WalletNotFoundError):
        StatementService(wallet_repo, ledger).get_statement(uuid.uuid4(), START, END)


def test_a_ledger_that_does_not_add_up_is_logged_as_an_error(wallet_repo, wallet, caplog):
    # 100 + 10 - 0 = 110, but the ledger claims the closing balance is 150.
    ledger = make_ledger_repo(
        Decimal("100.00"),
        Decimal("150.00"),
        PeriodTotals(Decimal("10.00"), Decimal("0"), 1),
    )

    with caplog.at_level(logging.ERROR, logger="app.services.statement_service"):
        statement = StatementService(wallet_repo, ledger).get_statement(wallet.user_id, START, END)

    assert "statement_reconciliation_mismatch" in caplog.text
    assert statement.closing_balance == Decimal("150.00")  # still returned
