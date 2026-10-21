"""Per-request context shared between the middleware and the services."""
from contextvars import ContextVar

# The caller's IP address. The middleware sets it; AuditService reads it.
client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)