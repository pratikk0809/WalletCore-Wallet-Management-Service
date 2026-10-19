"""Database access for wallets. No business rules here."""
import uuid
from collections.abc import Sequence

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

    def get_by_ids_for_update(self, wallet_ids: Sequence[uuid.UUID]) -> list[Wallet]:
        """Lock several wallet rows at once, always in ascending ID order.

        The fixed order is what prevents deadlocks between opposite transfers
        (A->B and B->A). Wallets that don't exist are simply missing from the result.
        """
        return list(
            self.db.scalars(
                select(Wallet)
                .where(Wallet.id.in_(wallet_ids))
                .order_by(Wallet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def add(self, wallet: Wallet) -> Wallet:
        """Stage the wallet and send the INSERT (flush) without committing."""
        self.db.add(wallet)
        self.db.flush()
        return wallet