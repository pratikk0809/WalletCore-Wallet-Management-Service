"""Integration tests for rate limiting (real PostgreSQL, fake clock)."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.rate_limit import rate_limiter

PASSWORD = "S3cret!pass"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(rate_limiter, "_clock", clock)
    return clock


@pytest.fixture(autouse=True)
def small_limits(monkeypatch, clock):
    """Switch limiting back on (conftest turns it off) with small limits."""
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "login_rate_limit", 3)
    monkeypatch.setattr(settings, "register_rate_limit", 4)
    monkeypatch.setattr(settings, "money_rate_limit", 3)


def register(client, email):
    return client.post("/auth/register", json={"email": email, "password": PASSWORD})


def login(client, email, password=PASSWORD):
    return client.post("/auth/login", data={"username": email, "password": password})


def make_user(client, email):
    """Register + login. Returns (auth headers, wallet id)."""
    register(client, email)
    token = login(client, email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, client.get("/wallets/me", headers=headers).json()["id"]


def deposit(client, headers, key, amount="10.00"):
    return client.post(
        "/wallets/deposit",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def withdraw(client, headers, key, amount="1.00"):
    return client.post(
        "/wallets/withdraw",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def transfer(client, headers, to_wallet_id, key, amount="1.00"):
    return client.post(
        "/transfers",
        json={"destination_wallet_id": to_wallet_id, "amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


# ---------- login ----------


def test_login_is_blocked_after_too_many_attempts_and_the_block_is_not_audited(client, engine):
    register(client, "alice@example.com")
    for _ in range(3):
        assert login(client, "alice@example.com", "wrong-pass1").status_code == 401

    blocked = login(client, "alice@example.com")  # even the correct password

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert blocked.headers["X-Request-ID"]
    with engine.connect() as conn:
        failed = conn.scalar(text("SELECT count(*) FROM audit_logs WHERE action = 'login_failed'"))
    assert failed == 3  # the blocked attempt never reached the service


def test_retry_after_shrinks_as_time_passes_and_login_works_after_the_window(client, clock):
    register(client, "alice@example.com")
    for _ in range(3):
        login(client, "alice@example.com", "wrong-pass1")

    clock.advance(20)
    blocked = login(client, "alice@example.com")
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "40"

    clock.advance(41)  # 61 seconds after the first attempt
    assert login(client, "alice@example.com").status_code == 200


# ---------- register ----------


def test_register_is_limited_per_ip(client):
    for i in range(4):
        assert register(client, f"user{i}@example.com").status_code == 201

    blocked = register(client, "user4@example.com")

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_register_and_login_use_separate_budgets(client):
    for i in range(4):
        register(client, f"user{i}@example.com")
    assert register(client, "user4@example.com").status_code == 429

    assert login(client, "user0@example.com").status_code == 200


# ---------- money endpoints ----------


def test_deposit_withdraw_and_transfer_share_one_per_user_budget(client):
    alice, _ = make_user(client, "alice@example.com")
    _, bob_wallet = make_user(client, "bob@example.com")

    assert deposit(client, alice, "d1").status_code == 201
    assert deposit(client, alice, "d2").status_code == 201
    assert withdraw(client, alice, "w1").status_code == 201

    blocked = transfer(client, alice, bob_wallet, "t1")

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert client.get("/wallets/me", headers=alice).json()["balance"] == "19.00"


def test_the_money_budget_is_per_user(client):
    alice, _ = make_user(client, "alice@example.com")
    bob, _ = make_user(client, "bob@example.com")
    for i in range(3):
        deposit(client, alice, f"a{i}")
    assert deposit(client, alice, "a3").status_code == 429

    assert deposit(client, bob, "b1").status_code == 201


def test_requests_without_a_token_never_use_up_a_budget(client):
    alice, _ = make_user(client, "alice@example.com")

    for _ in range(10):
        response = client.post(
            "/wallets/deposit",
            json={"amount": "10.00"},
            headers={"Idempotency-Key": "k"},
        )
        assert response.status_code == 401  # never 429

    assert deposit(client, alice, "d1").status_code == 201  # budget untouched


def test_read_only_endpoints_are_not_limited(client):
    alice, _ = make_user(client, "alice@example.com")

    for _ in range(10):
        assert client.get("/wallets/me", headers=alice).status_code == 200
        assert client.get("/transactions", headers=alice).status_code == 200


def test_parallel_requests_cannot_beat_the_limit(client):
    alice, _ = make_user(client, "alice@example.com")

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda i: deposit(client, alice, f"p{i}"), range(8)))

    assert sorted(r.status_code for r in responses) == [201] * 3 + [429] * 5
    assert client.get("/wallets/me", headers=alice).json()["balance"] == "30.00"


# ---------- switch ----------


def test_limits_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    register(client, "alice@example.com")

    for _ in range(10):
        assert login(client, "alice@example.com", "wrong-pass1").status_code == 401
