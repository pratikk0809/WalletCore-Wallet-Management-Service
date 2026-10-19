"""Public exports for the exceptions package."""
from app.exceptions.errors import (
    AppError,
    AuthenticationError,
    DuplicateTransactionError,
    EmailAlreadyRegisteredError,
    InsufficientFundsError,
    LimitExceededError,
    ServiceUnavailableError,
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
    "InsufficientFundsError",
    "LimitExceededError",
    "ServiceUnavailableError",
    "ValidationError",
    "WalletFrozenError",
    "WalletNotFoundError",
    "register_exception_handlers",
]