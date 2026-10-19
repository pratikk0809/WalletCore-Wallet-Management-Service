"""Business logic for wallets. Never imports FastAPI."""
import logging
import uuid

from app.exceptions import WalletNotFoundError
from app.models.wallet import Wallet
from app.repositories.wallet_repo import WalletRepository

logger = logging.getLogger(__name__)


class WalletService:
    def __init__(self, wallet_repo: WalletRepository) -> None:
        self.wallet_repo = wallet_repo

    def get_my_wallet(self, user_id: uuid.UUID) -> Wallet:
        wallet = self.wallet_repo.get_by_user_id(user_id)
        if wallet is None:
            # Should never happen: registration creates the wallet. If it does, it's a bug.
            logger.error("wallet_missing_for_user", extra={"user_id": str(user_id)})
            raise WalletNotFoundError()
        return wallet