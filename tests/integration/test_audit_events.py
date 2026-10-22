"""Integration tests: which actions produce audit rows (real PostgreSQL)."""

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

PASSWORD = "S3cret!pass"


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


def audit_rows(engine, action=None):
    sql = (
        "SELECT action, actor_id, entity_type, entity_id, metadata AS details, "
        "ip_address FROM audit_logs"
    )
    params = {}
    if action:
        sql += " WHERE action = :action"
        params["action"] = action
    sql += " ORDER BY created_at"
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def test_register_writes_one_user_registered_row(client, engine):
    body = client.post(
        "/auth/register", json={"email": "alice@example.com", "password": PASSWORD}
    ).json()

    rows = audit_rows(engine)

    assert len(rows) == 1
    row = rows[0]
    assert row.action == "user_registered" and row.entity_type == "user"
    assert str(row.actor_id) == body["id"] and str(row.entity_id) == body["id"]
    assert "wallet_id" in row.details


def test_registering_a_taken_email_writes_no_extra_row(client, engine):
    client.post("/auth/register", json={"email": "alice@example.com", "password": PASSWORD})
    client.post("/auth/register", json={"email": "alice@example.com", "password": PASSWORD})

    assert len(audit_rows(engine, "user_registered")) == 1


def test_successful_login_writes_a_login_succeeded_row(client, engine):
    _, user_id, _ = make_user(client, "alice@example.com")

    rows = audit_rows(engine, "login_succeeded")

    assert len(rows) == 1 and str(rows[0].actor_id) == user_id


def test_wrong_password_writes_a_login_failed_row_with_the_user(client, engine):
    _, user_id, _ = make_user(client, "alice@example.com")

    response = client.post(
        "/auth/login", data={"username": "alice@example.com", "password": "wrong-pass1"}
    )

    assert response.status_code == 401
    rows = audit_rows(engine, "login_failed")
    assert len(rows) == 1
    assert str(rows[0].actor_id) == user_id
    assert rows[0].details == {"reason": "wrong_password"}


def test_unknown_email_writes_a_login_failed_row_without_storing_the_email(client, engine):
    response = client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": "wrong-pass1"}
    )

    assert response.status_code == 401
    rows = audit_rows(engine, "login_failed")
    assert len(rows) == 1
    assert rows[0].actor_id is None and rows[0].entity_id is None
    assert rows[0].details == {"reason": "unknown_email"}
    assert "nobody@example.com" not in str(audit_rows(engine))


def test_disabled_account_login_is_audited(client, engine):
    make_user(client, "alice@example.com")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET status = 'disabled'"))

    response = client.post(
        "/auth/login", data={"username": "alice@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401
    rows = audit_rows(engine, "login_failed")
    assert rows[0].details == {"reason": "account_disabled"}


def test_deposit_writes_one_row_and_a_replay_writes_none(client, engine):
    headers, user_id, wallet_id = make_user(client, "alice@example.com")

    first = deposit(client, headers, "100.00", "same-key")
    replay = deposit(client, headers, "100.00", "same-key")

    assert first.status_code == 201 and replay.status_code == 200
    rows = audit_rows(engine, "deposit_completed")
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_type == "transaction"
    assert str(row.entity_id) == first.json()["id"]
    assert str(row.actor_id) == user_id
    assert row.details["amount"] == "100.00" and row.details["type"] == "deposit"
    assert row.details["destination_wallet_id"] == wallet_id
    assert row.details["source_wallet_id"] is None


def test_withdrawal_writes_one_row(client, engine):
    headers, user_id, wallet_id = make_user(client, "alice@example.com")
    deposit(client, headers, "100.00")

    response = withdraw(client, headers, "30.00", "w1")

    rows = audit_rows(engine, "withdrawal_completed")
    assert len(rows) == 1
    assert str(rows[0].entity_id) == response.json()["id"]
    assert rows[0].details["source_wallet_id"] == wallet_id
    assert rows[0].details["amount"] == "30.00"


def test_transfer_writes_one_row_for_the_sender(client, engine):
    alice, alice_id, alice_wallet = make_user(client, "alice@example.com")
    bob, _, bob_wallet = make_user(client, "bob@example.com")
    deposit(client, alice, "100.00")

    response = transfer(client, alice, bob_wallet, "40.00", "t1")

    rows = audit_rows(engine, "transfer_completed")
    assert len(rows) == 1
    row = rows[0]
    assert str(row.entity_id) == response.json()["id"]
    assert str(row.actor_id) == alice_id
    assert row.details["source_wallet_id"] == alice_wallet
    assert row.details["destination_wallet_id"] == bob_wallet


def test_rejected_operations_write_no_audit_row(client, engine):
    headers, _, _ = make_user(client, "alice@example.com")

    response = withdraw(client, headers, "10.00", "w1")  # empty wallet

    assert response.status_code == 422
    assert audit_rows(engine, "withdrawal_completed") == []


def test_read_only_requests_write_no_audit_rows(client, engine):
    headers, _, _ = make_user(client, "alice@example.com")
    before = len(audit_rows(engine))

    client.get("/wallets/me", headers=headers)
    client.get("/wallets/me/limits", headers=headers)
    client.get("/transactions", headers=headers)

    assert len(audit_rows(engine)) == before


def test_parallel_requests_with_the_same_key_write_exactly_one_row(client, engine):
    headers, _, _ = make_user(client, "alice@example.com")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda _: deposit(client, headers, "10.00", "race-key"), range(5))
        )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert len(audit_rows(engine, "deposit_completed")) == 1


def test_the_client_ip_is_recorded(client, engine):
    client.post("/auth/register", json={"email": "alice@example.com", "password": PASSWORD})

    row = audit_rows(engine)[0]

    assert row.ip_address and len(row.ip_address) <= 45
