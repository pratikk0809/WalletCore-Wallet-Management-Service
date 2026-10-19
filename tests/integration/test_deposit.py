"""Integration tests for POST /wallets/deposit against a real PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text

EMAIL = "alice@example.com"
PASSWORD = "S3cret!pass"


def auth_headers(client, email=EMAIL, password=PASSWORD) -> dict[str, str]:
    """Register + login, and return the Authorization header."""
    client.post("/auth/register", json={"email": email, "password": password})
    response = client.post("/auth/login", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def deposit(client, headers, amount="100.00", key="key-1", **extra):
    return client.post(
        "/wallets/deposit",
        json={"amount": amount, **extra},
        headers={**headers, "Idempotency-Key": key},
    )


def balance(client, headers) -> str:
    return client.get("/wallets/me", headers=headers).json()["balance"]


def test_deposit_returns_201_and_credits_wallet(client):
    headers = auth_headers(client)

    response = deposit(client, headers, "100.50", reference="first top-up")

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "deposit"
    assert body["status"] == "completed"
    assert body["amount"] == "100.50"
    assert body["currency"] == "INR"
    assert body["source_wallet_id"] is None
    assert body["reference"] == "first top-up"
    assert balance(client, headers) == "100.50"


def test_deposit_writes_balanced_ledger_entries(client, engine):
    headers = auth_headers(client)
    deposit(client, headers, "100.00")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT direction, amount, wallet_id, balance_after "
                "FROM ledger_entries ORDER BY direction"
            )
        ).all()

    assert [r.direction for r in rows] == ["credit", "debit"]
    assert rows[0].amount == rows[1].amount == Decimal("100.00")
    assert rows[0].wallet_id is not None and rows[1].wallet_id is None
    assert rows[0].balance_after == Decimal("100.00") and rows[1].balance_after is None


def test_deposits_accumulate_and_ledger_reconciles_with_balance(client, engine):
    headers = auth_headers(client)
    deposit(client, headers, "10.00", key="k1")
    deposit(client, headers, "20.25", key="k2")

    assert balance(client, headers) == "30.25"

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT w.balance AS balance, "
                "COALESCE(SUM(CASE WHEN l.direction = 'credit' THEN l.amount "
                "ELSE -l.amount END), 0) AS ledger_total "
                "FROM wallets w LEFT JOIN ledger_entries l ON l.wallet_id = w.id "
                "GROUP BY w.id"
            )
        ).one()
    assert row.balance == row.ledger_total == Decimal("30.25")


def test_replay_with_same_key_returns_original_and_deposits_once(client, engine):
    headers = auth_headers(client)
    first = deposit(client, headers, "100.00", key="same-key")
    second = deposit(client, headers, "100.00", key="same-key")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert balance(client, headers) == "100.00"  # NOT 200.00

    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM transactions")) == 1


def test_same_key_with_different_amount_returns_409(client):
    headers = auth_headers(client)
    deposit(client, headers, "100.00", key="same-key")

    response = deposit(client, headers, "999.00", key="same-key")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
    assert balance(client, headers) == "100.00"


def test_missing_idempotency_key_returns_422(client):
    headers = auth_headers(client)

    response = client.post("/wallets/deposit", json={"amount": "10.00"}, headers=headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "idempotency-key"


@pytest.mark.parametrize("amount", ["10.001", "-5", "0", "abc"])
def test_invalid_amounts_return_422_and_change_nothing(client, amount):
    headers = auth_headers(client)

    response = deposit(client, headers, amount)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert balance(client, headers) == "0.00"


def test_deposit_without_token_returns_401(client):
    response = client.post(
        "/wallets/deposit",
        json={"amount": "10.00"},
        headers={"Idempotency-Key": "key-1"},
    )
    assert response.status_code == 401


def test_frozen_wallet_returns_403_and_balance_is_unchanged(client, engine):
    headers = auth_headers(client)
    with engine.begin() as conn:
        conn.execute(text("UPDATE wallets SET status = 'frozen'"))

    response = deposit(client, headers, "10.00")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WALLET_FROZEN"


def test_same_key_for_different_users_is_independent(client):
    alice = auth_headers(client, "alice@example.com")
    bob = auth_headers(client, "bob@example.com")

    assert deposit(client, alice, "10.00", key="shared").status_code == 201
    assert deposit(client, bob, "20.00", key="shared").status_code == 201
    assert balance(client, alice) == "10.00"
    assert balance(client, bob) == "20.00"


def test_concurrent_requests_with_same_key_deposit_only_once(client):
    headers = auth_headers(client)

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda _: deposit(client, headers, "10.00", key="race-key"), range(5))
        )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert balance(client, headers) == "10.00"


def test_concurrent_deposits_do_not_lose_updates(client):
    headers = auth_headers(client)

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(
                lambda i: deposit(client, headers, "10.00", key=f"key-{i}"), range(5)
            )
        )

    assert all(r.status_code == 201 for r in responses)
    assert balance(client, headers) == "50.00"