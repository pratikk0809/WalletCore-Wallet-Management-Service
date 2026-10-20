"""Custom business exceptions.

Services RAISE these. They never import FastAPI or think about HTTP details
beyond declaring which status code the error maps to.
"""


class AppError(Exception):
    """Base class for all expected (business-rule) errors."""

    status_code: int = 400
    code: str = "APP_ERROR"
    default_message: str = "An error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class ValidationError(AppError):
    """A business-rule validation failed (e.g. amount has 3 decimal places).

    Note: this is NOT pydantic's ValidationError. Always import it from app.exceptions.
    """

    status_code = 422
    code = "VALIDATION_ERROR"
    default_message = "The request is invalid."


class WalletNotFoundError(AppError):
    status_code = 404
    code = "WALLET_NOT_FOUND"
    default_message = "Wallet not found."


class WalletFrozenError(AppError):
    status_code = 403
    code = "WALLET_FROZEN"
    default_message = "Wallet is frozen."


class InsufficientFundsError(AppError):
    status_code = 422
    code = "INSUFFICIENT_FUNDS"
    default_message = "Wallet balance is too low."


class LimitExceededError(AppError):
    status_code = 422
    code = "LIMIT_EXCEEDED"
    default_message = "Transaction limit exceeded."


class DuplicateTransactionError(AppError):
    """Idempotency key reused in a conflicting way (e.g. same key, different payload).

    A normal replay of the same request returns the ORIGINAL result instead.
    """

    status_code = 409
    code = "DUPLICATE_TRANSACTION"
    default_message = "This idempotency key was already used for a different request."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    default_message = "Service temporarily unavailable."
    

class AuthenticationError(AppError):
    """Missing, invalid, or expired credentials/token."""

    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "Could not validate credentials."


class EmailAlreadyRegisteredError(AppError):
    status_code = 409
    code = "EMAIL_ALREADY_REGISTERED"
    default_message = "An account with this email already exists."

class TransactionNotFoundError(AppError):
    status_code = 404
    code = "TRANSACTION_NOT_FOUND"
    default_message = "Transaction not found."