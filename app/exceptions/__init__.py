"""Public exports for the exceptions package."""
from app.exceptions.errors import (
    AppError,
    AuthenticationError,
    DuplicateTransactionError,
    EmailAlreadyRegisteredError,
    ForbiddenError,
    InsufficientFundsError,
    LimitExceededError,
    ServiceUnavailableError,
    TransactionNotFoundError,
    ValidationError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.exceptions.handlers import register_exception_handlers

__all__ = [
    "AppError",
    "AuthenticationError",
    "DuplicateTransactionError",
    "EmailAlreadyRegisteredError",
    "ForbiddenError",
    "InsufficientFundsError",
    "LimitExceededError",
    "ServiceUnavailableError",
    "TransactionNotFoundError",
    "ValidationError",
    "WalletFrozenError",
    "WalletNotFoundError",
    "register_exception_handlers",
]