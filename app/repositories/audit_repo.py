"""Database access for audit logs. Append-only: there is no update or delete."""

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, entry: AuditLog) -> AuditLog:
        """Stage the entry and send the INSERT (flush) without committing, so it
        is saved together with whatever operation the caller is doing."""
        self.db.add(entry)
        self.db.flush()
        return entry
