"""Rate-limit dependencies. Add one to a route with `dependencies=[Depends(...)]`."""

import logging

from fastapi import Depends

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.rate_limit import rate_limiter
from app.core.request_context import client_ip_ctx
from app.exceptions import RateLimitedError
from app.models.user import User

logger = logging.getLogger(__name__)


def _enforce(bucket: str, key: str, limit: int) -> None:
    """Count this request against `bucket:key`; raise 429 if over the limit."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    retry_after = rate_limiter.hit(f"{bucket}:{key}", limit, settings.rate_limit_window_seconds)
    if retry_after is not None:
        # The key (an IP or user ID) is deliberately not logged.
        logger.warning("rate_limited", extra={"bucket": bucket, "retry_after": retry_after})
        raise RateLimitedError(retry_after=retry_after)


def _client_ip() -> str:
    return client_ip_ctx.get() or "unknown"


def limit_login() -> None:
    """Login attempts, per client IP."""
    _enforce("login", _client_ip(), get_settings().login_rate_limit)


def limit_register() -> None:
    """Account creation, per client IP."""
    _enforce("register", _client_ip(), get_settings().register_rate_limit)


def limit_money(user: User = Depends(get_current_user)) -> None:
    """Deposits, withdrawals and transfers, per user (one shared bucket).

    Depends on get_current_user, so a request without a valid token gets 401
    first and never uses up anyone's budget. FastAPI reuses the same user
    lookup for the route itself, so there is no extra database query.
    """
    _enforce("money", str(user.id), get_settings().money_rate_limit)
