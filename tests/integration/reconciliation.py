"""A ledger auditor: finds accounting problems anywhere in the database.

`find_ledger_problems(engine)` returns a list of human-readable problems.
An empty list means the books are consistent."""
from sqlalchemy import text

# 1. Every wallet balance must equal credits minus debits in its ledger entries.
WALLET_VS_LEDGER = """
SELECT w.id, w.balance, l.total AS ledger_total
FROM wallets w
LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount ELSE -amount END), 0) AS total
    FROM ledger_entries WHERE wallet_id = w.id
) l ON TRUE
WHERE w.balance <> l.total
"""

# 2. No wallet may ever be negative (the CHECK constraint should make this impossible).
NEGATIVE_WALLETS = "SELECT id, balance FROM wallets WHERE balance < 0"

# 3. Every transaction must have equal total debits and credits.
UNBALANCED_TRANSACTIONS = """
SELECT transaction_id,
       COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'), 0) AS debits,
       COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0) AS credits
FROM ledger_entries
GROUP BY transaction_id
HAVING COALESCE(SUM(amount) FILTER (WHERE direction = 'debit'), 0)
    <> COALESCE(SUM(amount) FILTER (WHERE direction = 'credit'), 0)
"""

# 4. Every transaction has exactly two ledger entries (deposit, withdrawal and transfer all do).
WRONG_ENTRY_COUNT = """
SELECT t.id, count(l.id) AS entries
FROM transactions t LEFT JOIN ledger_entries l ON l.transaction_id = t.id
GROUP BY t.id HAVING count(l.id) <> 2
"""

# 5. Ledger entries carry the same amount as their transaction.
AMOUNT_MISMATCH = """
SELECT l.id, l.amount AS entry_amount, t.amount AS transaction_amount
FROM ledger_entries l JOIN transactions t ON t.id = l.transaction_id
WHERE l.amount <> t.amount
"""

# 6. Running balances form an unbroken chain per wallet: each balance_after equals
#    the previous balance_after plus/minus the entry amount (the first starts from 0).
BROKEN_RUNNING_BALANCE = """
WITH ordered AS (
    SELECT wallet_id, id, direction, amount, balance_after,
           LAG(balance_after, 1, 0::numeric) OVER (
               PARTITION BY wallet_id ORDER BY created_at, id
           ) AS previous
    FROM ledger_entries WHERE wallet_id IS NOT NULL
)
SELECT wallet_id, id FROM ordered
WHERE balance_after <> previous + CASE WHEN direction = 'credit' THEN amount ELSE -amount END
"""

# 7. Every transaction has exactly one audit row.
AUDIT_COUNT = """
SELECT t.id, count(a.id) AS audit_rows
FROM transactions t
LEFT JOIN audit_logs a ON a.entity_id = t.id AND a.entity_type = 'transaction'
GROUP BY t.id HAVING count(a.id) <> 1
"""


def find_ledger_problems(engine) -> list[str]:
    problems: list[str] = []
    with engine.connect() as conn:
        for r in conn.execute(text(WALLET_VS_LEDGER)):
            problems.append(
                f"wallet {r.id}: balance {r.balance} does not match ledger total {r.ledger_total}"
            )
        for r in conn.execute(text(NEGATIVE_WALLETS)):
            problems.append(f"wallet {r.id}: negative balance {r.balance}")
        for r in conn.execute(text(UNBALANCED_TRANSACTIONS)):
            problems.append(
                f"transaction {r.transaction_id}: unbalanced "
                f"(debits {r.debits}, credits {r.credits})"
            )
        for r in conn.execute(text(WRONG_ENTRY_COUNT)):
            problems.append(f"transaction {r.id}: has {r.entries} ledger entries (expected 2)")
        for r in conn.execute(text(AMOUNT_MISMATCH)):
            problems.append(
                f"ledger entry {r.id}: amount {r.entry_amount} differs from "
                f"transaction amount {r.transaction_amount}"
            )
        for r in conn.execute(text(BROKEN_RUNNING_BALANCE)):
            problems.append(f"wallet {r.wallet_id}: running balance breaks at entry {r.id}")
        for r in conn.execute(text(AUDIT_COUNT)):
            problems.append(f"transaction {r.id}: has {r.audit_rows} audit rows (expected 1)")
    return problems