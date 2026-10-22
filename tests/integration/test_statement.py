"""Integration tests for GET /wallets/me/statement (real PostgreSQL)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

PASSWORD = "S3cret!pass"


def auth_headers(client, email) -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_user(client, email):
    """Register + login. Returns (auth headers, wallet id)."""
    headers = auth_headers(client, email)
    return headers, client.get("/wallets/me", headers=headers).json()["id"]


def deposit(client, headers, amount, key, **extra):
    return client.post(
        "/wallets/deposit",
        json={"amount": amount, **extra},
        headers={**headers, "Idempotency-Key": key},
    )


def withdraw(client, headers, amount, key):
    return client.post(
        "/wallets/withdraw",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def transfer(client, headers, to_wallet_id, amount, key):
    return client.post(
        "/transfers",
        json={"destination_wallet_id": to_wallet_id, "amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def now() -> datetime:
    return datetime.now(timezone.utc)


def statement(client, headers, start=None, end=None, **extra):
    """Default period: from yesterday to tomorrow (covers everything just created)."""
    params = {
        "period_start": (start or now() - timedelta(days=1)).isoformat(),
        "period_end": (end or now() + timedelta(days=1)).isoformat(),
        **extra,
    }
    return client.get("/wallets/me/statement", headers=headers, params=params)


def set_ledger_time(engine, transaction_id, when):
    """Pretend a transaction's ledger entries were written at `when`."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ledger_entries SET created_at = :ts "
                "WHERE transaction_id = CAST(:id AS uuid)"
            ),
            {"ts": when, "id": transaction_id},
        )


def test_new_wallet_has_an_empty_statement(client):
    alice, wallet_id = make_user(client, "alice@example.com")

    response = statement(client, alice)

    assert response.status_code == 200
    body = response.json()
    assert body["wallet_id"] == wallet_id and body["currency"] == "INR"
    assert body["opening_balance"] == body["closing_balance"] == "0.00"
    assert body["total_credits"] == body["total_debits"] == "0.00"
    assert body["entry_count"] == 0 and body["truncated"] is False
    assert body["entries"] == []


def test_statement_lists_own_lines_oldest_first_with_running_balance(client):
    alice, _ = make_user(client, "alice@example.com")
    bob, bob_wallet = make_user(client, "bob@example.com")
    deposit(client, alice, "100.00", "d1", reference="salary")
    withdraw(client, alice, "30.00", "w1")
    transfer(client, alice, bob_wallet, "20.00", "t1")

    body = statement(client, alice).json()
    entries = body["entries"]

    # Only Alice's wallet lines: the external side of deposit/withdrawal is not shown.
    assert [e["type"] for e in entries] == ["deposit", "withdrawal", "transfer"]
    assert [e["direction"] for e in entries] == ["credit", "debit", "debit"]
    assert [e["amount"] for e in entries] == ["100.00", "30.00", "20.00"]
    assert [e["balance_after"] for e in entries] == ["100.00", "70.00", "50.00"]
    assert entries[0]["reference"] == "salary"
    assert body["opening_balance"] == "0.00" and body["closing_balance"] == "50.00"
    assert body["total_credits"] == "100.00" and body["total_debits"] == "50.00"
    assert body["entry_count"] == 3 and body["truncated"] is False
    # The ledger adds up.
    assert Decimal(body["opening_balance"]) + Decimal(body["total_credits"]) - Decimal(
        body["total_debits"]
    ) == Decimal(body["closing_balance"])


def test_recipient_sees_the_transfer_as_a_credit_and_outsiders_see_nothing(client):
    alice, _ = make_user(client, "alice@example.com")
    bob, bob_wallet = make_user(client, "bob@example.com")
    carol, _ = make_user(client, "carol@example.com")
    deposit(client, alice, "100.00", "d1")
    transfer(client, alice, bob_wallet, "20.00", "t1")

    bob_body = statement(client, bob).json()

    assert [e["type"] for e in bob_body["entries"]] == ["transfer"]
    assert bob_body["entries"][0]["direction"] == "credit"
    assert bob_body["entries"][0]["balance_after"] == "20.00"
    assert bob_body["closing_balance"] == "20.00"
    assert statement(client, carol).json()["entries"] == []


def test_opening_balance_comes_from_entries_before_the_period(client, engine):
    alice, _ = make_user(client, "alice@example.com")
    old = deposit(client, alice, "100.00", "d1").json()["id"]
    set_ledger_time(engine, old, now() - timedelta(days=100))
    recent = deposit(client, alice, "50.00", "d2").json()["id"]

    earlier = statement(
        client, alice, start=now() - timedelta(days=200), end=now() - timedelta(days=50)
    ).json()
    later = statement(
        client, alice, start=now() - timedelta(days=50), end=now() + timedelta(days=1)
    ).json()

    assert [e["transaction_id"] for e in earlier["entries"]] == [old]
    assert earlier["opening_balance"] == "0.00" and earlier["closing_balance"] == "100.00"
    assert [e["transaction_id"] for e in later["entries"]] == [recent]
    assert later["opening_balance"] == "100.00" and later["closing_balance"] == "150.00"


def test_period_start_is_inclusive_and_period_end_is_exclusive(client, engine):
    alice, _ = make_user(client, "alice@example.com")
    boundary = now().replace(microsecond=0) - timedelta(days=10)
    txn = deposit(client, alice, "10.00", "d1").json()["id"]
    set_ledger_time(engine, txn, boundary)

    starts_here = statement(client, alice, start=boundary, end=boundary + timedelta(days=1)).json()
    ends_here = statement(client, alice, start=boundary - timedelta(days=1), end=boundary).json()

    assert [e["transaction_id"] for e in starts_here["entries"]] == [txn]
    assert starts_here["opening_balance"] == "0.00"
    assert ends_here["entries"] == [] and ends_here["closing_balance"] == "0.00"


def test_limit_truncates_the_lines_but_not_the_totals(client):
    alice, _ = make_user(client, "alice@example.com")
    for i, amount in enumerate(["10.00", "20.00", "30.00"]):
        deposit(client, alice, amount, f"d{i}")

    body = statement(client, alice, limit=2).json()

    assert body["truncated"] is True
    assert [e["balance_after"] for e in body["entries"]] == ["10.00", "30.00"]  # oldest two
    assert body["entry_count"] == 3
    assert body["total_credits"] == "60.00" and body["closing_balance"] == "60.00"


def test_period_start_after_period_end_returns_422(client):
    alice, _ = make_user(client, "alice@example.com")

    response = statement(client, alice, start=now(), end=now() - timedelta(hours=1))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_period_longer_than_a_year_returns_422(client):
    alice, _ = make_user(client, "alice@example.com")

    response = statement(client, alice, start=now() - timedelta(days=400), end=now())

    assert response.status_code == 422
    assert "366" in response.json()["error"]["message"]


def test_missing_period_end_returns_422(client):
    alice, _ = make_user(client, "alice@example.com")

    response = client.get(
        "/wallets/me/statement",
        headers=alice,
        params={"period_start": now().isoformat()},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "period_end"


@pytest.mark.parametrize("limit", ["0", "1001"])
def test_invalid_limit_returns_422(client, limit):
    alice, _ = make_user(client, "alice@example.com")

    assert statement(client, alice, limit=limit).status_code == 422


def test_statement_requires_login(client):
    response = client.get(
        "/wallets/me/statement",
        params={
            "period_start": (now() - timedelta(days=1)).isoformat(),
            "period_end": now().isoformat(),
        },
    )
    assert response.status_code == 401


def test_parallel_deposits_still_show_a_consistent_running_balance(client):
    """The reason for clock_timestamp(): even when requests overlap, the lines are
    in the same order as the balance changes (10, 20, 30, 40, 50)."""
    alice, _ = make_user(client, "alice@example.com")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda i: deposit(client, alice, "10.00", f"k-{i}"), range(5)))

    assert all(r.status_code == 201 for r in responses)
    body = statement(client, alice).json()
    assert [e["balance_after"] for e in body["entries"]] == [
        "10.00",
        "20.00",
        "30.00",
        "40.00",
        "50.00",
    ]
    assert body["closing_balance"] == "50.00"
