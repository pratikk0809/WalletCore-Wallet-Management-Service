"""Database access for ledger entries. Append-only: there is no update or delete."""
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.ledger_entry import LedgerEntry


class LedgerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_many(self, entries: Sequence[LedgerEntry]) -> None:
        """Stage several entries at once (both sides of a transaction)."""
        self.db.add_all(entries)
        self.db.flush()