"""API metadata and docs switches. Documentation only."""

DESCRIPTION = """
A wallet and transaction service: accounts, deposits, withdrawals, transfers,
history, statements and audit logging.

**Authentication.** `POST /auth/login`, then send `Authorization: Bearer <token>`
(use the *Authorize* button above). Tokens last 30 minutes.

**Money.** Amounts are strings with at most 2 decimals (`"250.75"`), never floats.
All money is in the wallet currency (INR).

**Idempotency.** Every deposit, withdrawal and transfer needs an `Idempotency-Key`
header. Repeating a request with the same key and body returns the original result
(`200` + `Idempotent-Replayed: true`) and never moves money twice. The same key with a
different body gives `409`.

**Errors.** Every error has the same shape:
`{"error": {"code": "...", "message": "...", "request_id": "..."}}`. Validation errors
add a `details` list. Quote the `request_id` when reporting a problem.

**Rate limits.** Login and register are limited per IP, money endpoints per user.
Over the limit you get `429` with a `Retry-After` header.
"""

TAGS = [
    {"name": "health", "description": "Liveness and database connectivity check."},
    {"name": "auth", "description": "Create an account (with its wallet) and log in."},
    {"name": "wallets", "description": "Your wallet: balance, daily limit, statement, deposit and withdraw."},
    {"name": "transfers", "description": "Send money to another wallet, by wallet ID."},
    {"name": "transactions", "description": "Your transaction history (cursor pagination) and details."},
    {"name": "admin", "description": "Admin only: freeze or unfreeze wallets. Returns 403 for normal users."},
]


def docs_urls(environment: str) -> dict[str, str | None]:
    """Interactive docs and the schema are served only in local runs."""
    local = environment == "local"
    return {
        "docs_url": "/docs" if local else None,
        "redoc_url": "/redoc" if local else None,
        "openapi_url": "/openapi.json" if local else None,
    }
