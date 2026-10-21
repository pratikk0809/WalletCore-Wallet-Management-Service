"""Shared FastAPI dependencies. This file is also where we wire services together."""
from collections.abc import Generator

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.exceptions import AuthenticationError
from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.ledger_repo import LedgerRepository
from app.repositories.transaction_repo import TransactionRepository
from app.repositories.user_repo import UserRepository
from app.repositories.wallet_repo import WalletRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.history_service import HistoryService
from app.services.limit_service import LimitService
from app.services.statement_service import StatementService
from app.services.transaction_service import TransactionService
from app.services.wallet_service import WalletService

# tokenUrl powers the "Authorize" button in Swagger (/docs).
# auto_error=False: we raise our own AuthenticationError, so the response uses our standard error format.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_db() -> Generator[Session, None, None]:
    """Give each request its own DB session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_audit_service(db: Session = Depends(get_db)) -> AuditService:
    return AuditService(AuditRepository(db))


def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    """Build an AuthService with its repositories (dependency injection)."""
    return AuthService(
        db, UserRepository(db), WalletRepository(db), AuditService(AuditRepository(db))
    )


def get_wallet_service(db: Session = Depends(get_db)) -> WalletService:
    return WalletService(WalletRepository(db))


def get_limit_service(db: Session = Depends(get_db)) -> LimitService:
    return LimitService(TransactionRepository(db))


def get_history_service(db: Session = Depends(get_db)) -> HistoryService:
    return HistoryService(WalletRepository(db), TransactionRepository(db))


def get_statement_service(db: Session = Depends(get_db)) -> StatementService:
    return StatementService(WalletRepository(db), LedgerRepository(db))


def get_transaction_service(db: Session = Depends(get_db)) -> TransactionService:
    transaction_repo = TransactionRepository(db)
    return TransactionService(
        db,
        WalletRepository(db),
        transaction_repo,
        LedgerRepository(db),
        LimitService(transaction_repo),
        AuditService(AuditRepository(db)),
    )


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    service: AuthService = Depends(get_auth_service),
) -> User:
    """Protects a route: add `user: User = Depends(get_current_user)` to require login."""
    if token is None:
        raise AuthenticationError("Not authenticated.")
    return service.get_user_from_token(token)