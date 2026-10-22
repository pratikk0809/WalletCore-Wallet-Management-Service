"""Business logic for registration, login and token authentication. Never imports FastAPI."""

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.exceptions import AuthenticationError, EmailAlreadyRegisteredError
from app.models.enums import AuditAction, UserStatus
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.user_repo import UserRepository
from app.repositories.wallet_repo import WalletRepository
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)
settings = get_settings()


def _normalize_email(email: str) -> str:
    """We always store and look up emails lowercased and trimmed."""
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        db: Session,
        user_repo: UserRepository,
        wallet_repo: WalletRepository,
        audit_service: AuditService,
    ) -> None:
        self.db = db
        self.user_repo = user_repo
        self.wallet_repo = wallet_repo
        self.audit_service = audit_service

    def register(self, email: str, password: str) -> User:
        """Create a user AND their wallet (plus the audit row) in one DB transaction."""
        email = _normalize_email(email)

        # Friendly early check. The unique constraint below is the real guarantee.
        if self.user_repo.get_by_email(email) is not None:
            logger.warning("register_email_taken")
            raise EmailAlreadyRegisteredError()

        user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(password))
        wallet = Wallet(id=uuid.uuid4(), user_id=user.id, currency=settings.default_currency)

        try:
            self.user_repo.add(user)
            self.wallet_repo.add(wallet)
            # Staged in the same DB transaction: no user without an audit row.
            self.audit_service.record(
                AuditAction.USER_REGISTERED,
                entity_type="user",
                entity_id=user.id,
                actor_id=user.id,
                metadata={"wallet_id": wallet.id},
            )
            self.db.commit()  # user + wallet + audit row saved together, or not at all
        except IntegrityError:
            # Two requests registered the same email at the same moment;
            # the database unique constraint caught it.
            self.db.rollback()
            logger.warning("register_email_taken")
            raise EmailAlreadyRegisteredError()

        self.db.refresh(user)  # load DB-generated values such as created_at
        # Log IDs, never the email (PII).
        logger.info(
            "user_registered",
            extra={"user_id": str(user.id), "wallet_id": str(wallet.id)},
        )
        return user

    def login(self, email: str, password: str) -> str:
        """Check credentials and return a JWT access token."""
        user = self.user_repo.get_by_email(_normalize_email(email))

        if user is None:
            # Burn the same time as a real check so timing doesn't reveal
            # which emails exist.
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise self._login_failed(None, "unknown_email", "Incorrect email or password.")

        if not verify_password(password, user.password_hash):
            raise self._login_failed(user, "wrong_password", "Incorrect email or password.")

        if user.status != UserStatus.ACTIVE:
            raise self._login_failed(user, "account_disabled", "Account is disabled.")

        self.audit_service.record(
            AuditAction.LOGIN_SUCCEEDED,
            entity_type="user",
            entity_id=user.id,
            actor_id=user.id,
        )
        self.db.commit()
        logger.info("login_succeeded", extra={"user_id": str(user.id)})
        return create_access_token(str(user.id))

    def _login_failed(self, user: User | None, reason: str, message: str) -> AuthenticationError:
        """Audit a failed login and return the error for the caller to raise.

        Nothing else is being saved, so we commit the audit row ourselves;
        otherwise the end-of-request rollback would erase it.
        The email is never stored: only the reason, the user ID (if known) and the IP.
        """
        self.audit_service.record(
            AuditAction.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id if user else None,
            actor_id=user.id if user else None,
            metadata={"reason": reason},
        )
        self.db.commit()
        logger.warning(
            "login_failed",
            extra={"reason": reason, "user_id": str(user.id) if user else None},
        )
        return AuthenticationError(message)

    def get_user_from_token(self, token: str) -> User:
        """Turn a bearer token into an active User, or raise AuthenticationError.

        Every failure gives the same generic message so attackers learn nothing.
        """
        subject = decode_access_token(token)  # raises on bad/expired/forged tokens

        try:
            user_id = uuid.UUID(subject)
        except ValueError:
            raise AuthenticationError("Invalid or expired token.")

        user = self.user_repo.get_by_id(user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            # Covers: user deleted, or disabled after the token was issued.
            logger.warning("token_rejected", extra={"reason": "user_missing_or_disabled"})
            raise AuthenticationError("Invalid or expired token.")
        return user
