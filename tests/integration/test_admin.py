"""Integration tests for POST /admin/wallets/{id}/freeze (real PostgreSQL)."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

PASSWORD = "S3cret!pass"
ZERO_UUID = "00000000-0000-4000-8000-000000000000"


def make_user(client, email):
    """Register + login. Returns (auth headers, user id, wallet id)."""
    user_id = client.post("/auth/register", json={"email": email, "password": PASSWORD}).json()[
        "id"
    ]
    token = client.post("/auth/login", data={"username": email, "password": PASSWORD}).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    wallet_id = client.get("/wallets/me", headers=headers).json()["id"]
    return headers, user_id, wallet_id


def make_admin(engine, user_id):
    """There is no public way to become admin: promote the user in the database."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'admin' WHERE id = CAST(:id AS uuid)"),
            {"id": user_id},
        )


def set_frozen(client, headers, wallet_id, frozen=True, **extra):
    return client.post(
        f"/admin/wallets/{wallet_id}/freeze",
        json={"frozen": frozen, **extra},
        headers=headers,
    )


def deposit(client, headers, amount="100.00", key="d1"):
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


def transfer(client, headers, to_wallet_id, amount, key):
    return client.post(
        "/transfers",
        json={"destination_wallet_id": to_wallet_id, "amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def audit_rows(engine, action):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT action, actor_id, entity_type, entity_id, metadata AS details "
                "FROM audit_logs WHERE action = :action ORDER BY created_at"
            ),
            {"action": action},
        ).all()


@pytest.fixture
def admin(client, engine):
    """A logged-in admin: returns (headers, user id)."""
    headers, user_id, _ = make_user(client, "admin@example.com")
    make_admin(engine, user_id)  # the role is read from the DB on every request
    return headers, user_id


# ---------- access control ----------


def test_a_normal_user_gets_403_and_nothing_changes(client, engine):
    alice, _, alice_wallet = make_user(client, "alice@example.com")
    bob, _, _ = make_user(client, "bob@example.com")

    response = set_frozen(client, bob, alice_wallet)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert client.get("/wallets/me", headers=alice).json()["status"] == "active"
    assert audit_rows(engine, "wallet_frozen") == []


def test_freeze_requires_login(client):
    response = client.post(f"/admin/wallets/{ZERO_UUID}/freeze", json={"frozen": True})
    assert response.status_code == 401


# ---------- freeze / unfreeze ----------


def test_admin_can_freeze_a_wallet_and_the_owner_can_still_read_it(client, admin):
    admin_headers, _ = admin
    bob, bob_id, bob_wallet = make_user(client, "bob@example.com")
    deposit(client, bob, "50.00")

    response = set_frozen(client, admin_headers, bob_wallet, reason="suspected fraud")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == bob_wallet and body["user_id"] == bob_id
    assert body["status"] == "frozen" and body["changed"] is True
    assert body["balance"] == "50.00" and body["currency"] == "INR"
    assert client.get("/wallets/me", headers=bob).json()["status"] == "frozen"
    assert client.get("/transactions", headers=bob).status_code == 200  # reads still work


def test_a_frozen_wallet_cannot_deposit_or_withdraw(client, admin):
    admin_headers, _ = admin
    bob, _, bob_wallet = make_user(client, "bob@example.com")
    deposit(client, bob, "50.00", "fund")
    set_frozen(client, admin_headers, bob_wallet)

    dep = deposit(client, bob, "10.00", "d2")
    wd = withdraw(client, bob, "10.00", "w1")

    assert dep.status_code == wd.status_code == 403
    assert dep.json()["error"]["code"] == wd.json()["error"]["code"] == "WALLET_FROZEN"
    assert client.get("/wallets/me", headers=bob).json()["balance"] == "50.00"


def test_a_frozen_wallet_cannot_send_or_receive_transfers(client, admin):
    admin_headers, _ = admin
    bob, _, bob_wallet = make_user(client, "bob@example.com")
    carol, _, carol_wallet = make_user(client, "carol@example.com")
    deposit(client, bob, "100.00", "fund-b")
    deposit(client, carol, "100.00", "fund-c")
    set_frozen(client, admin_headers, bob_wallet)

    sending = transfer(client, bob, carol_wallet, "10.00", "t1")
    receiving = transfer(client, carol, bob_wallet, "10.00", "t2")

    assert sending.status_code == receiving.status_code == 403
    assert "Your wallet" in sending.json()["error"]["message"]
    assert "Destination" in receiving.json()["error"]["message"]
    assert client.get("/wallets/me", headers=carol).json()["balance"] == "100.00"


def test_unfreezing_restores_normal_operation(client, admin):
    admin_headers, _ = admin
    bob, _, bob_wallet = make_user(client, "bob@example.com")
    set_frozen(client, admin_headers, bob_wallet, True)

    response = set_frozen(client, admin_headers, bob_wallet, False)

    assert response.status_code == 200
    assert response.json()["status"] == "active" and response.json()["changed"] is True
    assert deposit(client, bob, "10.00", "d1").status_code == 201


def test_repeating_a_freeze_changes_nothing_and_writes_one_audit_row(client, admin, engine):
    admin_headers, _ = admin
    _, _, bob_wallet = make_user(client, "bob@example.com")

    first = set_frozen(client, admin_headers, bob_wallet)
    second = set_frozen(client, admin_headers, bob_wallet)

    assert first.json()["changed"] is True
    assert second.status_code == 200 and second.json()["changed"] is False
    assert len(audit_rows(engine, "wallet_frozen")) == 1


# ---------- request errors ----------


def test_unknown_wallet_returns_404(client, admin):
    admin_headers, _ = admin

    response = set_frozen(client, admin_headers, ZERO_UUID)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WALLET_NOT_FOUND"


def test_malformed_wallet_id_returns_422(client, admin):
    admin_headers, _ = admin

    assert set_frozen(client, admin_headers, "not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},  # frozen is required
        {"frozen": "yes"},  # not a real boolean
        {"frozen": 1},  # numbers are not accepted either
        {"frozen": True, "reason": "x" * 256},  # reason too long
    ],
)
def test_invalid_bodies_return_422_and_change_nothing(client, admin, body):
    admin_headers, _ = admin
    bob, _, bob_wallet = make_user(client, "bob@example.com")

    response = client.post(f"/admin/wallets/{bob_wallet}/freeze", json=body, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/wallets/me", headers=bob).json()["status"] == "active"


# ---------- audit trail ----------


def test_freeze_writes_an_audit_row_with_admin_wallet_and_reason(client, admin, engine):
    admin_headers, admin_id = admin
    _, bob_id, bob_wallet = make_user(client, "bob@example.com")

    set_frozen(client, admin_headers, bob_wallet, reason="suspected fraud")

    rows = audit_rows(engine, "wallet_frozen")
    assert len(rows) == 1
    row = rows[0]
    assert str(row.actor_id) == admin_id
    assert row.entity_type == "wallet" and str(row.entity_id) == bob_wallet
    assert row.details == {
        "previous_status": "active",
        "new_status": "frozen",
        "reason": "suspected fraud",
        "owner_user_id": bob_id,
    }


def test_unfreeze_writes_a_wallet_unfrozen_row(client, admin, engine):
    admin_headers, _ = admin
    _, _, bob_wallet = make_user(client, "bob@example.com")
    set_frozen(client, admin_headers, bob_wallet, True)

    set_frozen(client, admin_headers, bob_wallet, False)

    rows = audit_rows(engine, "wallet_unfrozen")
    assert len(rows) == 1
    assert rows[0].details["previous_status"] == "frozen"
    assert rows[0].details["new_status"] == "active"
    assert rows[0].details["reason"] is None


# ---------- concurrency ----------


def test_parallel_freezes_change_the_wallet_once_and_write_one_audit_row(client, admin, engine):
    admin_headers, _ = admin
    _, _, bob_wallet = make_user(client, "bob@example.com")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda _: set_frozen(client, admin_headers, bob_wallet), range(5))
        )

    assert all(r.status_code == 200 for r in responses)
    assert sorted(r.json()["changed"] for r in responses) == [False] * 4 + [True]
    assert len(audit_rows(engine, "wallet_frozen")) == 1
