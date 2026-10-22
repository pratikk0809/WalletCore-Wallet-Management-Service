"""Unit tests for AuthService.get_user_from_token (mocked repositories)."""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from app.core.security import create_access_token
from app.exceptions import AuthenticationError
from app.models.user import User
from app.services.auth_service import AuthService


@pytest.fixture
def user_repo():
    return MagicMock()


@pytest.fixture
def service(user_repo):
    return AuthService(MagicMock(), user_repo, MagicMock(), MagicMock())


def make_user(status: str = "active") -> User:
    return User(id=uuid.uuid4(), email="alice@example.com", password_hash="x", status=status)


def test_valid_token_returns_user(service, user_repo):
    user = make_user()
    user_repo.get_by_id.return_value = user

    result = service.get_user_from_token(create_access_token(str(user.id)))

    assert result is user
    user_repo.get_by_id.assert_called_once_with(user.id)


def test_garbage_token_raises(service):
    with pytest.raises(AuthenticationError):
        service.get_user_from_token("not-a-jwt")


def test_expired_token_raises(service):
    token = create_access_token(str(uuid.uuid4()), expires_delta=timedelta(seconds=-1))
    with pytest.raises(AuthenticationError):
        service.get_user_from_token(token)


def test_subject_that_is_not_a_uuid_raises(service, user_repo):
    with pytest.raises(AuthenticationError):
        service.get_user_from_token(create_access_token("not-a-uuid"))
    user_repo.get_by_id.assert_not_called()


def test_token_for_unknown_user_raises(service, user_repo):
    user_repo.get_by_id.return_value = None
    with pytest.raises(AuthenticationError):
        service.get_user_from_token(create_access_token(str(uuid.uuid4())))


def test_token_for_disabled_user_raises(service, user_repo):
    user = make_user(status="disabled")
    user_repo.get_by_id.return_value = user
    with pytest.raises(AuthenticationError):
        service.get_user_from_token(create_access_token(str(user.id)))
