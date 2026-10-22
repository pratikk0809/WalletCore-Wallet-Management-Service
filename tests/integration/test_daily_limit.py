"""Integration tests for the daily limit, using a real PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings

EMAIL = "alice@example.com"
PASSWORD = "S3cret!pass"


@pytest.fixture(autouse=True)
def low_daily_limit(monkeypatch):
    """Use a small limit (100.00) for every test in this file."""
    monkeypatch.setattr(get_settings(), "daily_limit", Decimal("100.00"))


def auth_headers(client, email=EMAIL, password=PASSWORD) -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": password})
    response = client.post("/auth/login", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def deposit(client, headers, amount, key):
    return client.post(
        "/wallets/deposit",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def withdraw(client, headers, amount, key):
    return client.post(
        "/wallets/withdraw",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def balance(client, headers) -> str:
    return client.get("/wallets/me", headers=headers).json()["balance"]


def funded_user(client, amount="500.00") -> dict[str, str]:
    headers = auth_headers(client)
    deposit(client, headers, amount, "fund-1")
    return headers


def test_withdrawals_stop_at_the_daily_limit(client):
    headers = funded_user(client)

    assert withdraw(client, headers, "60.00", "w1").status_code == 201
    over = withdraw(client, headers, "50.00", "w2")  # 60 + 50 > 100

    assert over.status_code == 422
    error = over.json()["error"]
    assert error["code"] == "LIMIT_EXCEEDED"
    assert "Remaining today: 40.00" in error["message"]
    assert balance(client, headers) == "440.00"  # the rejected one took nothing

    assert withdraw(client, headers, "40.00", "w3").status_code == 201  # exactly 100
    assert withdraw(client, headers, "0.01", "w4").status_code == 422


def test_deposits_do_not_count_towards_the_limit(client):
    headers = funded_user(client, "500.00")
    deposit(client, headers, "1000.00", "big-deposit")

    assert withdraw(client, headers, "100.00", "w1").status_code == 201


def test_replaying_a_completed_withdrawal_still_works_after_the_limit_is_used_up(client):
    headers = funded_user(client)
    first = withdraw(client, headers, "100.00", "same-key")  # uses the whole limit
    replay = withdraw(client, headers, "100.00", "same-key")

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]


def test_limits_endpoint_shows_usage(client):
    headers = funded_user(client)

    before = client.get("/wallets/me/limits", headers=headers).json()
    assert before["daily_limit"] == "100.00"
    assert before["used_today"] == "0.00"
    assert before["remaining_today"] == "100.00"

    withdraw(client, headers, "60.00", "w1")

    after = client.get("/wallets/me/limits", headers=headers).json()
    assert after["used_today"] == "60.00"
    assert after["remaining_today"] == "40.00"
    assert after["resets_at"]


def test_limits_endpoint_requires_login(client):
    assert client.get("/wallets/me/limits").status_code == 401


def test_withdrawals_from_a_previous_day_do_not_count(client, engine):
    headers = funded_user(client)
    assert withdraw(client, headers, "100.00", "w1").status_code == 201

    # Pretend that withdrawal happened two days ago.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE transactions SET created_at = now() - interval '2 days' "
                "WHERE type = 'withdrawal'"
            )
        )

    assert withdraw(client, headers, "100.00", "w2").status_code == 201


def test_parallel_withdrawals_cannot_beat_the_daily_limit(client):
    """Balance 500, limit 100, five parallel withdrawals of 30: only three fit."""
    headers = funded_user(client, "500.00")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda i: withdraw(client, headers, "30.00", f"wd-{i}"), range(5))
        )

    assert sorted(r.status_code for r in responses) == [201, 201, 201, 422, 422]
    assert balance(client, headers) == "410.00"  # 500 - 3 x 30
    used = client.get("/wallets/me/limits", headers=headers).json()["used_today"]
    assert used == "90.00"
