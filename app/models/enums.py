"""Allowed values for status/role/type columns.

We store these as plain strings in the DB (not native Postgres ENUMs),
because adding a value to a native ENUM later is painful in migrations.
A CHECK constraint on each column keeps the values valid.
"""
from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class WalletStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"


class TransactionType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


class LedgerDirection(StrEnum):
    DEBIT = "debit"    # money leaves the wallet
    CREDIT = "credit"  # money enters the wallet