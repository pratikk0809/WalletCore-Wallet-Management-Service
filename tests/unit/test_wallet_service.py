"""Unit tests for WalletService (mocked repository)."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.exceptions import WalletNotFoundError
from app.models.wallet import Wallet
from app.services.wallet_service import WalletService


def test_get_my_wallet_returns_wallet():
    user_id = uuid.uuid4()
    wallet = Wallet(id=uuid.uuid4(), user_id=user_id, currency="INR", balance=Decimal("0.00"))
    repo = MagicMock()
    repo.get_by_user_id.return_value = wallet

    assert WalletService(repo).get_my_wallet(user_id) is wallet
    repo.get_by_user_id.assert_called_once_with(user_id)


def test_get_my_wallet_missing_raises():
    repo = MagicMock()
    repo.get_by_user_id.return_value = None

    with pytest.raises(WalletNotFoundError):
        WalletService(repo).get_my_wallet(uuid.uuid4())
