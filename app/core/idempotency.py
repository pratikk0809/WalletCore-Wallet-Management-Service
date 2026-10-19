"""Request fingerprinting for idempotency. Pure function: no DB, no FastAPI."""
import hashlib
import json
from decimal import Decimal


def compute_request_hash(
    txn_type: str,
    amount: Decimal,
    reference: str | None,
    destination: str | None = None,
) -> str:
    """SHA-256 fingerprint of what the client asked for.

    Same idempotency key + same fingerprint  -> replay (return the original).
    Same idempotency key + different one    -> conflict (409).
    `destination` is unused for deposits; transfers (Phase 4) will pass it.
    """
    canonical = json.dumps(
        {
            "type": txn_type,
            "amount": str(amount),  # already normalized to 2 places
            "reference": reference,
            "destination": destination,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()