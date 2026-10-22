"""The DATABASE itself must reject invalid transactions and ledger entries,
even if a bug in our Python code tried to create them."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import LedgerEntry, Transaction, User, Wallet


@pytest.fixture
def db(engine, client):
    """A DB session on the test database. Depending on `client` makes it
    empty all tables first (see conftest.py)."""
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.rollback()
    session.close()


def make_wallet(db, email: str) -> tuple[User, Wallet]:
    user = User(id=uuid.uuid4(), email=email, password_hash="x")
    wallet = Wallet(id=uuid.uuid4(), user_id=user.id, currency="INR")
    db.add(user)
    db.flush()  # users must exist before the wallet that points to them
    db.add(wallet)
    db.flush()
    return user, wallet


def make_deposit(user, wallet, key="key-1", amount="10.00") -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        type="deposit",
        status="completed",
        amount=Decimal(amount),
        currency="INR",
        destination_wallet_id=wallet.id,
        idempotency_key=key,
        request_hash="h" * 64,
    )


def test_valid_deposit_is_accepted(db):
    user, wallet = make_wallet(db, "a@example.com")
    db.add(make_deposit(user, wallet))
    db.flush()  # no error expected


def test_zero_amount_is_rejected(db):
    user, wallet = make_wallet(db, "a@example.com")
    db.add(make_deposit(user, wallet, amount="0.00"))
    with pytest.raises(IntegrityError, match="ck_transactions_amount_positive"):
        db.flush()


def test_deposit_with_a_source_wallet_is_rejected(db):
    user, wallet = make_wallet(db, "a@example.com")
    txn = make_deposit(user, wallet)
    txn.source_wallet_id = wallet.id  # deposits must have no source
    db.add(txn)
    with pytest.raises(IntegrityError, match="ck_transactions_wallet_shape_valid"):
        db.flush()


def test_self_transfer_is_rejected(db):
    user, wallet = make_wallet(db, "a@example.com")
    db.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            type="transfer",
            status="pending",
            amount=Decimal("5.00"),
            currency="INR",
            source_wallet_id=wallet.id,
            destination_wallet_id=wallet.id,
            idempotency_key="key-1",
            request_hash="h" * 64,
        )
    )
    with pytest.raises(IntegrityError, match="ck_transactions_wallet_shape_valid"):
        db.flush()


def test_same_user_cannot_reuse_an_idempotency_key(db):
    user, wallet = make_wallet(db, "a@example.com")
    db.add(make_deposit(user, wallet, key="same-key"))
    db.flush()

    db.add(make_deposit(user, wallet, key="same-key"))
    with pytest.raises(IntegrityError, match="uq_transactions_user_id_idempotency_key"):
        db.flush()


def test_different_users_can_use_the_same_idempotency_key(db):
    alice, alice_wallet = make_wallet(db, "a@example.com")
    bob, bob_wallet = make_wallet(db, "b@example.com")

    db.add(make_deposit(alice, alice_wallet, key="same-key"))
    db.add(make_deposit(bob, bob_wallet, key="same-key"))
    db.flush()  # no error expected


def test_ledger_wallet_entry_needs_a_balance_after(db):
    user, wallet = make_wallet(db, "a@example.com")
    txn = make_deposit(user, wallet)
    db.add(txn)
    db.flush()

    db.add(
        LedgerEntry(
            id=uuid.uuid4(),
            transaction_id=txn.id,
            wallet_id=wallet.id,
            direction="credit",
            amount=Decimal("10.00"),
            balance_after=None,  # a wallet entry without a balance snapshot
        )
    )
    with pytest.raises(IntegrityError, match="ck_ledger_entries_external_has_no_balance"):
        db.flush()


def test_ledger_external_and_wallet_entries_are_accepted(db):
    user, wallet = make_wallet(db, "a@example.com")
    txn = make_deposit(user, wallet)
    db.add(txn)
    db.flush()

    db.add_all(
        [
            LedgerEntry(  # wallet side: credit, with balance snapshot
                id=uuid.uuid4(),
                transaction_id=txn.id,
                wallet_id=wallet.id,
                direction="credit",
                amount=Decimal("10.00"),
                balance_after=Decimal("10.00"),
            ),
            LedgerEntry(  # external side: debit, no wallet, no balance
                id=uuid.uuid4(),
                transaction_id=txn.id,
                wallet_id=None,
                direction="debit",
                amount=Decimal("10.00"),
                balance_after=None,
            ),
        ]
    )
    db.flush()  # no error expected
