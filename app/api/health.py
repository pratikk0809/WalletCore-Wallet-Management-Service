"""Health check endpoint."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.responses import errors
from app.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    responses=errors(
        503,
        custom={
            503: ("The database is unreachable.", "SERVICE_UNAVAILABLE", "Database unavailable.")
        },
    ),
)
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """Returns 200 if the app is up AND can reach the database."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.error("health_db_check_failed", exc_info=True)
        raise ServiceUnavailableError("Database unavailable.")
    return {"status": "ok", "database": "up"}
