"""Immutability triggers, plus a run of the REAL Alembic migrations.

Uses its own scratch database (<db>_migrated) so the normal test database,
built with create_all, is not affected."""

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings

_dev_url = make_url(get_settings().database_url)
SCRATCH_DB = f"{_dev_url.database}_migrated"
_scratch_url = _dev_url.set(database=SCRATCH_DB)


def _recreate_scratch_database() -> None:
    admin = create_engine(_dev_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    admin.dispose()


@pytest.fixture(scope="module")
def migrated_engine():
    """An empty database brought up to date by the real `alembic upgrade head`."""
    _recreate_scratch_database()
    env = {**os.environ, "DATABASE_URL": _scratch_url.render_as_string(hide_password=False)}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scratch_url)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(migrated_engine):
    """A connection whose changes are always rolled back, so tests stay independent."""
    connection = migrated_engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()


def insert_user_wallet(conn):
    user_id, wallet_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )
    conn.execute(
        text("INSERT INTO wallets (id, user_id, currency) VALUES (:id, :uid, 'INR')"),
        {"id": wallet_id, "uid": user_id},
    )
    return user_id, wallet_id


def insert_transaction(conn, user_id, wallet_id, status="completed"):
    txn_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO transactions (id, user_id, type, status, amount, currency, "
            "destination_wallet_id, idempotency_key, request_hash) VALUES "
            "(:id, :uid, 'deposit', :status, 10.00, 'INR', :wid, :key, :hash)"
        ),
        {
            "id": txn_id,
            "uid": user_id,
            "status": status,
            "wid": wallet_id,
            "key": uuid.uuid4().hex,
            "hash": "h" * 64,
        },
    )
    return txn_id


def insert_ledger_entry(conn, txn_id, wallet_id):
    entry_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ledger_entries (id, transaction_id, wallet_id, direction, "
            "amount, balance_after) VALUES (:id, :tid, :wid, 'credit', 10.00, 10.00)"
        ),
        {"id": entry_id, "tid": txn_id, "wid": wallet_id},
    )
    return entry_id


def insert_audit_row(conn, user_id):
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO audit_logs (id, actor_id, action, entity_type) "
            "VALUES (:id, :uid, 'login_succeeded', 'user')"
        ),
        {"id": row_id, "uid": user_id},
    )
    return row_id


# ---------- the real migrations ----------


def test_real_migrations_create_all_tables_and_triggers(migrated_engine):
    with migrated_engine.connect() as conn:
        tables = set(
            conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        )
        triggers = set(conn.scalars(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")))

    assert {"users", "wallets", "transactions", "ledger_entries", "audit_logs"} <= tables
    assert {
        "trg_audit_logs_immutable",
        "trg_ledger_entries_immutable",
        "trg_transactions_no_delete",
        "trg_transactions_completed_immutable",
    } <= triggers


def test_real_migrations_keep_the_database_level_money_constraints(conn):
    user_id, wallet_id = insert_user_wallet(conn)

    with pytest.raises(IntegrityError, match="ck_wallets_balance_non_negative"):
        conn.execute(text("UPDATE wallets SET balance = -1 WHERE id = :id"), {"id": wallet_id})


# ---------- normal writes still work ----------


def test_inserts_are_allowed_everywhere(conn):
    user_id, wallet_id = insert_user_wallet(conn)
    txn_id = insert_transaction(conn, user_id, wallet_id)
    insert_ledger_entry(conn, txn_id, wallet_id)
    insert_audit_row(conn, user_id)  # no error expected


def test_wallets_can_still_be_updated(conn):
    _, wallet_id = insert_user_wallet(conn)

    conn.execute(
        text("UPDATE wallets SET balance = 5.00, status = 'frozen' WHERE id = :id"),
        {"id": wallet_id},
    )  # no error expected


def test_a_pending_transaction_can_still_be_updated(conn):
    user_id, wallet_id = insert_user_wallet(conn)
    txn_id = insert_transaction(conn, user_id, wallet_id, status="pending")

    conn.execute(
        text("UPDATE transactions SET status = 'completed' WHERE id = :id"),
        {"id": txn_id},
    )  # no error expected


# ---------- forbidden changes ----------


def test_ledger_entries_cannot_be_updated_or_deleted(conn):
    user_id, wallet_id = insert_user_wallet(conn)
    entry_id = insert_ledger_entry(conn, insert_transaction(conn, user_id, wallet_id), wallet_id)

    with pytest.raises(IntegrityError, match="rows are immutable"):
        conn.execute(text("UPDATE ledger_entries SET amount = 99 WHERE id = :id"), {"id": entry_id})


def test_ledger_entry_delete_is_rejected(conn):
    user_id, wallet_id = insert_user_wallet(conn)
    entry_id = insert_ledger_entry(conn, insert_transaction(conn, user_id, wallet_id), wallet_id)

    with pytest.raises(IntegrityError, match="rows are immutable"):
        conn.execute(text("DELETE FROM ledger_entries WHERE id = :id"), {"id": entry_id})


def test_audit_logs_cannot_be_updated(conn):
    user_id, _ = insert_user_wallet(conn)
    row_id = insert_audit_row(conn, user_id)

    with pytest.raises(IntegrityError, match="rows are immutable"):
        conn.execute(
            text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"), {"id": row_id}
        )


def test_audit_logs_cannot_be_deleted(conn):
    user_id, _ = insert_user_wallet(conn)
    row_id = insert_audit_row(conn, user_id)

    with pytest.raises(IntegrityError, match="rows are immutable"):
        conn.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": row_id})


def test_a_completed_transaction_cannot_be_updated(conn):
    user_id, wallet_id = insert_user_wallet(conn)
    txn_id = insert_transaction(conn, user_id, wallet_id, status="completed")

    with pytest.raises(IntegrityError, match="completed transactions"):
        conn.execute(text("UPDATE transactions SET amount = 99 WHERE id = :id"), {"id": txn_id})


def test_a_completed_transaction_cannot_be_marked_reversed_in_place(conn):
    user_id, wallet_id = insert_user_wallet(conn)
    txn_id = insert_transaction(conn, user_id, wallet_id, status="completed")

    with pytest.raises(IntegrityError, match="completed transactions"):
        conn.execute(
            text("UPDATE transactions SET status = 'reversed' WHERE id = :id"),
            {"id": txn_id},
        )


@pytest.mark.parametrize("status", ["pending", "completed"])
def test_transactions_can_never_be_deleted(conn, status):
    user_id, wallet_id = insert_user_wallet(conn)
    txn_id = insert_transaction(conn, user_id, wallet_id, status=status)

    with pytest.raises(IntegrityError, match="rows are immutable"):
        conn.execute(text("DELETE FROM transactions WHERE id = :id"), {"id": txn_id})
