"""Integration tests for POST /transfers against a real PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings

PASSWORD = "S3cret!pass"


def auth_headers(client, email) -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def deposit(client, headers, amount, key="fund-1"):
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


def transfer(client, headers, to_wallet_id, amount="30.00", key="tr-1", **extra):
    return client.post(
        "/transfers",
        json={"destination_wallet_id": to_wallet_id, "amount": amount, **extra},
        headers={**headers, "Idempotency-Key": key},
    )


def make_user(client, email, funded: str | None = None):
    """Register + login. Returns (auth headers, wallet id), optionally funded."""
    headers = auth_headers(client, email)
    wallet_id = client.get("/wallets/me", headers=headers).json()["id"]
    if funded:
        deposit(client, headers, funded)
    return headers, wallet_id


def balance(client, headers) -> str:
    return client.get("/wallets/me", headers=headers).json()["balance"]


def freeze(engine, wallet_id):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE wallets SET status = 'frozen' WHERE id = CAST(:id AS uuid)"),
            {"id": wallet_id},
        )


def test_transfer_returns_201_and_moves_money(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    response = transfer(client, alice, bob_wallet, "30.50", reference="lunch")

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "transfer"
    assert body["status"] == "completed"
    assert body["amount"] == "30.50"
    assert body["source_wallet_id"] is not None
    assert body["destination_wallet_id"] == bob_wallet
    assert body["reference"] == "lunch"
    assert balance(client, alice) == "69.50"
    assert balance(client, bob) == "30.50"


def test_transfer_ledger_is_balanced_and_every_wallet_reconciles(client, engine):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com", "20.00")
    transfer(client, alice, bob_wallet, "40.00")

    with engine.connect() as conn:
        entries = conn.execute(
            text(
                "SELECT l.direction, l.amount, l.wallet_id, l.balance_after "
                "FROM ledger_entries l JOIN transactions t ON t.id = l.transaction_id "
                "WHERE t.type = 'transfer' ORDER BY l.direction"
            )
        ).all()
        wallets = conn.execute(
            text(
                "SELECT w.balance AS balance, "
                "COALESCE(SUM(CASE WHEN l.direction = 'credit' THEN l.amount "
                "ELSE -l.amount END), 0) AS ledger_total "
                "FROM wallets w LEFT JOIN ledger_entries l ON l.wallet_id = w.id "
                "GROUP BY w.id"
            )
        ).all()

    assert [e.direction for e in entries] == ["credit", "debit"]
    assert entries[0].amount == entries[1].amount == Decimal("40.00")
    assert all(e.wallet_id is not None for e in entries)  # no external side
    assert entries[0].balance_after == Decimal("60.00")  # bob: 20 + 40
    assert entries[1].balance_after == Decimal("60.00")  # alice: 100 - 40
    assert all(w.balance == w.ledger_total for w in wallets)


def test_replay_returns_original_and_transfers_once(client, engine):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    first = transfer(client, alice, bob_wallet, "30.00", key="same-key")
    second = transfer(client, alice, bob_wallet, "30.00", key="same-key")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert balance(client, alice) == "70.00"  # NOT 40.00
    assert balance(client, bob) == "30.00"
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM transactions WHERE type = 'transfer'")) == 1


def test_same_key_with_different_amount_returns_409(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")
    transfer(client, alice, bob_wallet, "30.00", key="same-key")

    response = transfer(client, alice, bob_wallet, "50.00", key="same-key")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
    assert balance(client, alice) == "70.00"


def test_same_key_with_different_destination_returns_409(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")
    carol, carol_wallet = make_user(client, "carol@example.com")
    transfer(client, alice, bob_wallet, "30.00", key="same-key")

    response = transfer(client, alice, carol_wallet, "30.00", key="same-key")

    assert response.status_code == 409
    assert balance(client, carol) == "0.00"


def test_self_transfer_returns_422(client):
    alice, alice_wallet = make_user(client, "alice@example.com", "100.00")

    response = transfer(client, alice, alice_wallet, "10.00")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert balance(client, alice) == "100.00"


def test_unknown_destination_returns_404(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")

    response = transfer(client, alice, "00000000-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WALLET_NOT_FOUND"
    assert balance(client, alice) == "100.00"


def test_insufficient_funds_returns_422_and_stores_nothing(client, engine):
    alice, _ = make_user(client, "alice@example.com", "50.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    response = transfer(client, alice, bob_wallet, "50.01")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert balance(client, alice) == "50.00" and balance(client, bob) == "0.00"
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM transactions WHERE type = 'transfer'")) == 0


def test_frozen_sender_returns_403(client, engine):
    alice, alice_wallet = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")
    freeze(engine, alice_wallet)

    response = transfer(client, alice, bob_wallet, "10.00")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WALLET_FROZEN"


def test_frozen_recipient_returns_403_and_nothing_moves(client, engine):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")
    freeze(engine, bob_wallet)

    response = transfer(client, alice, bob_wallet, "10.00")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WALLET_FROZEN"
    assert balance(client, alice) == "100.00"


def test_transfers_stop_at_the_daily_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_limit", Decimal("100.00"))
    alice, _ = make_user(client, "alice@example.com", "500.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    assert transfer(client, alice, bob_wallet, "60.00", key="t1").status_code == 201
    over = transfer(client, alice, bob_wallet, "50.00", key="t2")

    assert over.status_code == 422
    assert over.json()["error"]["code"] == "LIMIT_EXCEEDED"
    assert balance(client, alice) == "440.00"


def test_transfers_and_withdrawals_share_one_daily_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_limit", Decimal("100.00"))
    alice, _ = make_user(client, "alice@example.com", "500.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    assert withdraw(client, alice, "60.00", "w1").status_code == 201
    over = transfer(client, alice, bob_wallet, "50.00", key="t1")  # 60 + 50 > 100

    assert over.status_code == 422
    assert over.json()["error"]["code"] == "LIMIT_EXCEEDED"


def test_malformed_destination_id_returns_422(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")

    response = transfer(client, alice, "not-a-uuid")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "destination_wallet_id"


def test_missing_idempotency_key_returns_422(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    response = client.post(
        "/transfers",
        json={"destination_wallet_id": bob_wallet, "amount": "10.00"},
        headers=alice,
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "idempotency-key"


def test_transfer_without_token_returns_401(client):
    response = client.post(
        "/transfers",
        json={
            "destination_wallet_id": "00000000-0000-4000-8000-000000000000",
            "amount": "10.00",
        },
        headers={"Idempotency-Key": "key-1"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("amount", ["10.001", "-5", "0"])
def test_invalid_amounts_return_422_and_change_nothing(client, amount):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    response = transfer(client, alice, bob_wallet, amount)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert balance(client, alice) == "100.00"


# ---------- concurrency ----------


def test_parallel_transfers_can_never_overdraw(client):
    """Balance 100, five parallel transfers of 30: only three can succeed."""
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(
                lambda i: transfer(client, alice, bob_wallet, "30.00", key=f"t-{i}"),
                range(5),
            )
        )

    assert sorted(r.status_code for r in responses) == [201, 201, 201, 422, 422]
    assert balance(client, alice) == "10.00"
    assert balance(client, bob) == "90.00"


def test_opposite_direction_transfers_do_not_deadlock(client):
    """Alice->Bob and Bob->Alice at the same time. Without ordered locking
    Postgres would abort some of these with a deadlock (HTTP 500)."""
    alice, alice_wallet = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com", "100.00")

    def send(i):
        if i % 2 == 0:
            return transfer(client, alice, bob_wallet, "10.00", key=f"ab-{i}")
        return transfer(client, bob, alice_wallet, "5.00", key=f"ba-{i}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(send, range(10)))

    assert all(r.status_code == 201 for r in responses)  # no 500s
    # Alice sent 5 x 10 and received 5 x 5; Bob the opposite.
    assert balance(client, alice) == "75.00"
    assert balance(client, bob) == "125.00"  # total money is still 200.00


def test_concurrent_requests_with_same_key_transfer_only_once(client):
    alice, _ = make_user(client, "alice@example.com", "100.00")
    bob, bob_wallet = make_user(client, "bob@example.com")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(
                lambda _: transfer(client, alice, bob_wallet, "10.00", key="race-key"),
                range(5),
            )
        )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert balance(client, alice) == "90.00"
    assert balance(client, bob) == "10.00"
