"""Writes audit log entries. Never imports FastAPI, never commits."""
import json
import uuid
from typing import Any

from app.core.request_context import client_ip_ctx
from app.models.audit_log import AuditLog
from app.models.enums import AuditAction
from app.repositories.audit_repo import AuditRepository

# Any metadata key containing one of these words is blanked out.
_SENSITIVE_WORDS = ("password", "token", "secret", "authorization", "email")
_REDACTED = "[redacted]"


def _redact(value: Any) -> Any:
    """Return a copy of `value` with sensitive keys blanked out (also in nested dicts)."""
    if isinstance(value, dict):
        return {
            key: _REDACTED
            if any(word in str(key).lower() for word in _SENSITIVE_WORDS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class AuditService:
    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    def record(
        self,
        action: AuditAction,
        *,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        """Stage one audit entry. The CALLER commits, so the entry is saved in
        the same DB transaction as the operation it describes.

        If no IP is given, the current request's client IP is used."""
        # Redact first, then make everything JSON-safe (Decimal, UUID, datetime
        # become strings) so a normal-looking dict can never crash the insert.
        clean = json.loads(json.dumps(_redact(metadata or {}), default=str))

        entry = AuditLog(
            id=uuid.uuid4(),
            actor_id=actor_id,
            action=action.value,
            entity_type=entity_type,
            entity_id=entity_id,
            details=clean,
            ip_address=ip_address or client_ip_ctx.get(),
        )
        return self.audit_repo.add(entry)