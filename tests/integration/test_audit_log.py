"""Integration tests for the audit_logs table (real PostgreSQL)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, User
from app.models.enums import AuditAction
from app.repositories.audit_repo import AuditRepository
from app.services.audit_service import AuditService


@pytest.fixture
def db(engine, client):
    """A DB session on the test database. Depending on `client` makes it
    empty all tables first (see conftest.py)."""
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def audit(db):
    return AuditService(AuditRepository(db))


def make_user(db) -> User:
    user = User(id=uuid.uuid4(), email="a@example.com", password_hash="x")
    db.add(user)
    db.flush()
    return user


def count_logs(db) -> int:
    return db.scalar(select(func.count()).select_from(AuditLog))


def test_entry_roundtrips_with_jsonb_metadata_and_server_timestamp(db, audit):
    user = make_user(db)
    entity = uuid.uuid4()

    audit.record(
        AuditAction.DEPOSIT_COMPLETED,
        entity_type="transaction",
        entity_id=entity,
        actor_id=user.id,
        metadata={"amount": Decimal("100.00"), "currency": "INR"},
        ip_address="2001:db8::1",
    )
    db.commit()

    row = db.scalars(select(AuditLog)).one()
    assert row.action == "deposit_completed"
    assert row.actor_id == user.id and row.entity_id == entity
    assert row.details == {"amount": "100.00", "currency": "INR"}
    assert row.ip_address == "2001:db8::1"
    assert row.created_at is not None  # filled in by the database


def test_event_without_a_known_user_is_allowed(db, audit):
    audit.record(
        AuditAction.LOGIN_FAILED,
        entity_type="user",
        metadata={"reason": "unknown_email"},
        ip_address="203.0.113.7",
    )
    db.commit()

    row = db.scalars(select(AuditLog)).one()
    assert row.actor_id is None and row.entity_id is None


def test_actor_must_be_an_existing_user(db, audit):
    # The row is flushed (sent to the DB) inside record(), so the foreign key
    # is checked right there, before any commit.
    with pytest.raises(IntegrityError, match="fk_audit_logs_actor_id_users"):
        audit.record(
            AuditAction.DEPOSIT_COMPLETED,
            entity_type="wallet",
            actor_id=uuid.uuid4(),
        )


def test_entry_is_saved_or_discarded_together_with_the_caller_transaction(db, audit):
    """The service never commits: rolling back the caller's transaction removes
    the audit row too. This is what makes 'money operation + audit row' atomic."""
    user = make_user(db)
    audit.record(AuditAction.USER_REGISTERED, entity_type="user", actor_id=user.id)
    assert count_logs(db) == 1  # visible inside the transaction

    db.rollback()

    assert count_logs(db) == 0