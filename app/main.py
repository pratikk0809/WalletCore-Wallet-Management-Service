"""FastAPI application entry point."""
import logging

from fastapi import FastAPI

from app.api import health
from app.api.v1 import admin, auth, transactions, transfers, wallets
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.exceptions import register_exception_handlers
from app.middleware.request_id import RequestIDMiddleware

settings = get_settings()

# Logging must be configured before anything else logs.
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

# Note: we don't pass debug=settings.debug to FastAPI. With debug on, Starlette
# shows its own HTML crash page and skips our global 500 handler.
app = FastAPI(title=settings.app_name)

app.add_middleware(RequestIDMiddleware)
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(wallets.router)
app.include_router(transfers.router)
app.include_router(transactions.router)
app.include_router(admin.router)

logger.info("app_started", extra={"environment": settings.environment})