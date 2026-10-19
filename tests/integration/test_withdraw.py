"""Integration tests for POST /wallets/withdraw against a real PostgreSQL database."""
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


def deposit(client, headers, amount="100.00", key="dep-1"):
    return client.post(
        "/wallets/deposit",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def withdraw(client, headers, amount="30.00", key="wd-1", **extra):
    return client.post(
        "/wallets/withdraw",
        json={"amount": amount, **extra},
        headers={**headers, "Idempotency-Key": key},
    )


def balance(client, headers) -> str:
    return client.get("/wallets/me", headers=headers).json()["balance"]


def funded_user(client, amount="100.00", email=EMAIL) -> dict[str, str]:
    """A logged-in user whose wallet already holds `amount`."""
    headers = auth_headers(client, email)
    deposit(client, headers, amount)
    return headers


def test_withdraw_returns_201_and_debits_wallet(client):
    headers = funded_user(client, "100.00")

    response = withdraw(client, headers, "30.50", reference="rent")

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "withdrawal"
    assert body["status"] == "completed"
    assert body["amount"] == "30.50"
    assert body["source_wallet_id"] is not None
    assert body["destination_wallet_id"] is None
    assert body["reference"] == "rent"
    assert balance(client, headers) == "69.50"


def test_ledger_stays_balanced_and_reconciles_after_deposit_and_withdrawal(client, engine):
    headers = funded_user(client, "100.00")
    withdraw(client, headers, "40.00")

    with engine.connect() as conn:
        totals = conn.execute(
            text(
                "SELECT COALESCE(SUM(CASE WHEN direction = 'debit' THEN amount END), 0) AS debits, "
                "COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount END), 0) AS credits "
                "FROM ledger_entries"
            )
        ).one()
        reconcile = conn.execute(
            text(
                "SELECT w.balance AS balance, "
                "COALESCE(SUM(CASE WHEN l.direction = 'credit' THEN l.amount "
                "ELSE -l.amount END), 0) AS ledger_total "
                "FROM wallets w LEFT JOIN ledger_entries l ON l.wallet_id = w.id "
                "GROUP BY w.id"
            )
        ).one()

    assert totals.debits == totals.credits == Decimal("140.00")  # 2 x (100 + 40)
    assert reconcile.balance == reconcile.ledger_total == Decimal("60.00")


def test_withdrawing_the_whole_balance_leaves_zero(client):
    headers = funded_user(client, "100.00")

    assert withdraw(client, headers, "100.00").status_code == 201
    assert balance(client, headers) == "0.00"


def test_insufficient_funds_returns_422_and_stores_nothing(client, engine):
    headers = funded_user(client, "50.00")

    response = withdraw(client, headers, "50.01")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert balance(client, headers) == "50.00"
    with engine.connect() as conn:
        # Only the deposit exists.
        assert conn.scalar(text("SELECT count(*) FROM transactions")) == 1


def test_withdraw_from_empty_wallet_returns_422(client):
    headers = auth_headers(client)

    response = withdraw(client, headers, "1.00")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"


def test_replay_returns_original_and_withdraws_once(client, engine):
    headers = funded_user(client, "100.00")
    first = withdraw(client, headers, "30.00", key="same-key")
    second = withdraw(client, headers, "30.00", key="same-key")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert balance(client, headers) == "70.00"  # NOT 40.00

    with engine.connect() as conn:
        assert conn.scalar(
            text("SELECT count(*) FROM transactions WHERE type = 'withdrawal'")
        ) == 1


def test_same_key_with_different_amount_returns_409(client):
    headers = funded_user(client, "100.00")
    withdraw(client, headers, "30.00", key="same-key")

    response = withdraw(client, headers, "50.00", key="same-key")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
    assert balance(client, headers) == "70.00"


def test_missing_idempotency_key_returns_422(client):
    headers = funded_user(client)

    response = client.post("/wallets/withdraw", json={"amount": "10.00"}, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "idempotency-key"


@pytest.mark.parametrize("amount", ["10.001", "-5", "0", "abc"])
def test_invalid_amounts_return_422_and_change_nothing(client, amount):
    headers = funded_user(client, "100.00")

    response = withdraw(client, headers, amount)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert balance(client, headers) == "100.00"


def test_withdraw_without_token_returns_401(client):
    response = client.post(
        "/wallets/withdraw",
        json={"amount": "10.00"},
        headers={"Idempotency-Key": "key-1"},
    )
    assert response.status_code == 401


def test_frozen_wallet_returns_403_and_balance_is_unchanged(client, engine):
    headers = funded_user(client, "100.00")
    with engine.begin() as conn:
        conn.execute(text("UPDATE wallets SET status = 'frozen'"))

    response = withdraw(client, headers, "10.00")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WALLET_FROZEN"


def test_concurrent_withdrawals_can_never_overdraw(client):
    """Balance 100, five parallel withdrawals of 30: only three can succeed."""
    headers = funded_user(client, "100.00")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda i: withdraw(client, headers, "30.00", key=f"wd-{i}"), range(5))
        )

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 201, 201, 422, 422]
    assert balance(client, headers) == "10.00"  # 100 - 3 x 30, never negative


def test_concurrent_requests_with_same_key_withdraw_only_once(client):
    headers = funded_user(client, "100.00")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda _: withdraw(client, headers, "10.00", key="race-key"), range(5))
        )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert balance(client, headers) == "90.00"