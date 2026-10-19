"""Database access for wallets. No business rules here."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.wallet import Wallet


class WalletRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_user_id(self, user_id: uuid.UUID) -> Wallet | None:
        return self.db.scalar(select(Wallet).where(Wallet.user_id == user_id))

    def get_by_user_id_for_update(self, user_id: uuid.UUID) -> Wallet | None:
        """Load the wallet AND lock its row until the DB transaction ends.

        Other requests touching this wallet wait here. populate_existing makes
        sure we read the fresh values, not a stale copy cached in the session.
        """
        return self.db.scalar(
            select(Wallet)
            .where(Wallet.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def add(self, wallet: Wallet) -> Wallet:
        """Stage the wallet and send the INSERT (flush) without committing."""
        self.db.add(wallet)
        self.db.flush()
        return wallet