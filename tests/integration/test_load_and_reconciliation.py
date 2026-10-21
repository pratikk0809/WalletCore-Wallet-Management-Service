"""Mixed-load concurrency tests, checked by the ledger auditor (real PostgreSQL)."""
import random
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.integration.reconciliation import find_ledger_problems

PASSWORD = "S3cret!pass"
USERS = 6
INITIAL = Decimal("200.00")
AMOUNTS = ["5.00", "12.50", "40.00", "75.25", "300.00"]  # 300 > INITIAL: some will be rejected

Op = namedtuple("Op", "kind user target amount key")


def make_user(client, email):
    """Register + login. Returns (auth headers, wallet id)."""
    client.post("/auth/register", json={"email": email, "password": PASSWORD})
    token = client.post(
        "/auth/login", data={"username": email, "password": PASSWORD}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, client.get("/wallets/me", headers=headers).json()["id"]


def deposit(client, headers, amount, key):
    return client.post(
        "/wallets/deposit", json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def withdraw(client, headers, amount, key):
    return client.post(
        "/wallets/withdraw", json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def transfer(client, headers, to_wallet_id, amount, key):
    return client.post(
        "/transfers", json={"destination_wallet_id": to_wallet_id, "amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def balance(client, headers) -> Decimal:
    return Decimal(client.get("/wallets/me", headers=headers).json()["balance"])


def funded_users(client, count, amount="1000.00"):
    """`count` logged-in users, each with `amount` already deposited."""
    people = []
    for i in range(count):
        headers, wallet_id = make_user(client, f"user{i}@example.com")
        assert deposit(client, headers, amount, "fund").status_code == 201
        people.append((headers, wallet_id))
    return people


def build_operations(seed):
    """A reproducible random workload, including repeated requests (replays)."""
    rng = random.Random(seed)
    ops = []
    for n in range(120):
        user = rng.randrange(USERS)
        ops.append(
            Op(
                kind=rng.choice(["deposit", "withdraw", "transfer", "transfer"]),
                user=user,
                target=rng.choice([i for i in range(USERS) if i != user]),
                amount=rng.choice(AMOUNTS),
                key=f"op-{n}",
            )
        )
    workload = ops + rng.sample(ops, 15)  # 15 requests are sent a second time
    rng.shuffle(workload)
    return workload


# ---------- mixed load ----------

@pytest.mark.parametrize("seed", [1, 2])
def test_mixed_parallel_load_keeps_the_books_consistent(client, engine, seed):
    people = funded_users(client, USERS, str(INITIAL))

    def run(op):
        headers, _ = people[op.user]
        if op.kind == "deposit":
            response = deposit(client, headers, op.amount, op.key)
        elif op.kind == "withdraw":
            response = withdraw(client, headers, op.amount, op.key)
        else:
            response = transfer(client, headers, people[op.target][1], op.amount, op.key)
        return op, response

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(run, build_operations(seed)))

    # Only success, replay, or "not enough money" may happen: never a 500 or 409.
    statuses = Counter(r.status_code for _, r in results)
    assert set(statuses) <= {200, 201, 422}, statuses
    assert 201 in statuses and 422 in statuses, statuses  # the workload really did both
    for _, r in results:
        if r.status_code == 422:
            assert r.json()["error"]["code"] == "INSUFFICIENT_FUNDS"

    # A repeated request can create at most ONE transaction.
    created = [(op, r) for op, r in results if r.status_code == 201]
    assert all(n == 1 for n in Counter(op.key for op, _ in created).values())

    # Independent cross-check: total money = start + deposits - withdrawals (transfers net to 0).
    expected_total = USERS * INITIAL
    for op, r in created:
        amount = Decimal(r.json()["amount"])
        if op.kind == "deposit":
            expected_total += amount
        elif op.kind == "withdraw":
            expected_total -= amount
    with engine.connect() as conn:
        actual_total = conn.scalar(text("SELECT COALESCE(SUM(balance), 0) FROM wallets"))
        stored = conn.scalar(text("SELECT count(*) FROM transactions"))
    assert actual_total == expected_total
    assert stored == len(created) + USERS  # + one funding deposit per user

    assert find_ledger_problems(engine) == []


def test_a_ring_of_transfers_does_not_deadlock(client, engine):
    """A->B, B->C and C->A at the same time: every wallet pair locks in ID order."""
    people = funded_users(client, 3)
    ring = [(0, 1), (1, 2), (2, 0)]

    def run(i):
        sender, receiver = ring[i % 3]
        return transfer(client, people[sender][0], people[receiver][1], "10.00", f"ring-{i}")

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(run, range(30)))

    assert all(r.status_code == 201 for r in responses)  # no deadlock errors
    assert [balance(client, p[0]) for p in people] == [Decimal("1000.00")] * 3
    assert find_ledger_problems(engine) == []


def test_many_senders_to_one_recipient_lose_no_money(client, engine):
    """A 'hot' wallet: five senders pour 20 transfers into the same recipient."""
    people = funded_users(client, 6)
    recipient_headers, recipient_wallet = people[0]
    senders = people[1:]

    def run(i):
        headers, _ = senders[i % 5]
        return transfer(client, headers, recipient_wallet, "25.00", f"hot-{i}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(run, range(20)))

    assert all(r.status_code == 201 for r in responses)
    assert balance(client, recipient_headers) == Decimal("1500.00")  # 1000 + 20 x 25
    assert [balance(client, h) for h, _ in senders] == [Decimal("900.00")] * 5
    assert find_ledger_problems(engine) == []


# ---------- the auditor itself: does it really notice corruption? ----------

def test_the_auditor_accepts_a_healthy_database(client, engine):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "100.00", "d1")
    withdraw(client, headers, "30.00", "w1")

    assert find_ledger_problems(engine) == []


def test_the_auditor_detects_a_wallet_balance_that_was_tampered_with(client, engine):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "100.00", "d1")
    with engine.begin() as conn:
        conn.execute(text("UPDATE wallets SET balance = balance + 1"))

    problems = find_ledger_problems(engine)

    assert any("does not match ledger total" in p for p in problems)


def test_the_auditor_detects_a_tampered_ledger_entry(client, engine):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "100.00", "d1")
    with engine.begin() as conn:  # this test database has no immutability triggers
        conn.execute(
            text("UPDATE ledger_entries SET amount = amount + 1 WHERE direction = 'credit'")
        )

    problems = find_ledger_problems(engine)

    assert any("unbalanced" in p for p in problems)
    assert any("differs from transaction amount" in p for p in problems)


def test_the_auditor_detects_a_missing_audit_row(client, engine):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "100.00", "d1")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_logs WHERE entity_type = 'transaction'"))

    problems = find_ledger_problems(engine)

    assert any("0 audit rows" in p for p in problems)