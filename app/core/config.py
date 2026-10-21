"""Application settings, loaded from environment variables / .env file."""
from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Wallet & Transaction Service"
    environment: str = "local"
    debug: bool = False
    log_level: str = "INFO"

    # Required: the app will refuse to start if this is missing.
    database_url: str

    # Required, and must be at least 32 characters, so a weak key can't slip in.
    secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Every new user gets one wallet in this currency (multi-currency is out of scope for v1).
    default_currency: str = "INR"

    # Per-transaction limits, applied to deposits, withdrawals and transfers.
    # Decimal (not float) so the values are exact.
    min_transaction_amount: Decimal = Decimal("0.01")
    max_transaction_amount: Decimal = Decimal("100000.00")

    # Max total a wallet may send out per UTC day (withdrawals + transfers).
    daily_limit: Decimal = Decimal("50000.00")

    # Rate limiting: max requests per window (seconds). Login/register are counted
    # per client IP, money endpoints per user. Counters are in memory (per process).
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    login_rate_limit: int = Field(default=10, gt=0)
    register_rate_limit: int = Field(default=5, gt=0)
    money_rate_limit: int = Field(default=30, gt=0)

    # Read from .env if present; ignore extra variables (e.g. POSTGRES_USER).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Create Settings once and reuse it (lru_cache = simple singleton)."""
    return Settings()