"""Password hashing and JWT helpers. Pure functions: no database, no FastAPI."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.exceptions.errors import AuthenticationError

settings = get_settings()

# Argon2id with safe default parameters.
_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return a salted Argon2 hash. This is what we store in users.password_hash."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True if `password` matches the stored hash."""
    return _hasher.verify(password, password_hash)


# Used at login when the email doesn't exist: we still run a hash check so the
# response time doesn't reveal which emails are registered (timing attack).
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT. `subject` is the user's ID as a string."""
    now = datetime.now(timezone.utc)
    expires = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": subject,             # who the token is for
        "iat": now,                 # issued at
        "exp": now + expires,       # expiry
        "jti": uuid.uuid4().hex,    # unique token ID (useful for revocation later)
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Validate a JWT and return its subject (user ID).

    Raises AuthenticationError if the token is malformed, tampered with,
    expired, or missing required fields.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],   # pinned: never trust the token's own "alg"
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        # Same message for every failure: don't tell attackers why it failed.
        raise AuthenticationError("Invalid or expired token.")
    return payload["sub"]