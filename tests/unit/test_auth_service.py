"""Unit tests for AuthService. Repositories and the DB session are mocked,
so these tests run without a database."""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.security import DUMMY_PASSWORD_HASH, decode_access_token, hash_password
from app.exceptions import AuthenticationError, EmailAlreadyRegisteredError
from app.models.user import User
from app.services.auth_service import AuthService

settings = get_settings()


@pytest.fixture
def db():
    return MagicMock()


@pytest.fixture
def user_repo():
    return MagicMock()


@pytest.fixture
def wallet_repo():
    return MagicMock()


@pytest.fixture
def service(db, user_repo, wallet_repo):
    return AuthService(db, user_repo, wallet_repo, MagicMock())


def make_user(status: str = "active") -> User:
    return User(
        id=uuid.uuid4(),
        email="alice@example.com",
        password_hash=hash_password("S3cret!pass"),
        status=status,
    )


# ---------- register ----------

def test_register_creates_user_and_wallet(service, db, user_repo, wallet_repo):
    user_repo.get_by_email.return_value = None

    user = service.register("alice@example.com", "S3cret!pass")

    assert user.email == "alice@example.com"
    assert user.password_hash != "S3cret!pass"  # stored hashed, never plaintext
    wallet = wallet_repo.add.call_args.args[0]
    assert wallet.user_id == user.id
    assert wallet.currency == settings.default_currency
    db.commit.assert_called_once()


def test_register_normalizes_email(service, user_repo):
    user_repo.get_by_email.return_value = None

    user = service.register("  Alice@Example.COM ", "S3cret!pass")

    assert user.email == "alice@example.com"
    user_repo.get_by_email.assert_called_with("alice@example.com")


def test_register_duplicate_email_raises(service, db, user_repo):
    user_repo.get_by_email.return_value = make_user()

    with pytest.raises(EmailAlreadyRegisteredError):
        service.register("alice@example.com", "S3cret!pass")

    user_repo.add.assert_not_called()
    db.commit.assert_not_called()


def test_register_race_condition_rolls_back(service, db, user_repo):
    # Email looked free, but the DB unique constraint fires on insert.
    user_repo.get_by_email.return_value = None
    user_repo.add.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    with pytest.raises(EmailAlreadyRegisteredError):
        service.register("alice@example.com", "S3cret!pass")

    db.rollback.assert_called_once()
    db.commit.assert_not_called()


# ---------- login ----------

def test_login_success_returns_token_for_user(service, user_repo):
    user = make_user()
    user_repo.get_by_email.return_value = user

    token = service.login("Alice@example.com", "S3cret!pass")

    assert decode_access_token(token) == str(user.id)


def test_login_wrong_password_raises(service, user_repo):
    user_repo.get_by_email.return_value = make_user()

    with pytest.raises(AuthenticationError):
        service.login("alice@example.com", "wrong-password")


def test_login_unknown_email_still_runs_a_hash_check(service, user_repo):
    user_repo.get_by_email.return_value = None

    with patch("app.services.auth_service.verify_password") as mock_verify:
        with pytest.raises(AuthenticationError):
            service.login("nobody@example.com", "whatever123")

    mock_verify.assert_called_once()
    assert mock_verify.call_args.args[1] == DUMMY_PASSWORD_HASH


def test_login_disabled_user_raises(service, user_repo):
    user_repo.get_by_email.return_value = make_user(status="disabled")

    with pytest.raises(AuthenticationError):
        service.login("alice@example.com", "S3cret!pass")