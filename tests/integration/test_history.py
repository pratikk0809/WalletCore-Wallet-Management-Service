"""Integration tests for GET /transactions and GET /transactions/{id}."""

import pytest
from sqlalchemy import text

PASSWORD = "S3cret!pass"
ZERO_UUID = "00000000-0000-4000-8000-000000000000"


def auth_headers(client, email) -> dict[str, str]:
    client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_user(client, email):
    """Register + login. Returns (auth headers, wallet id)."""
    headers = auth_headers(client, email)
    return headers, client.get("/wallets/me", headers=headers).json()["id"]


def deposit(client, headers, amount="1.00", key="dep-1"):
    return client.post(
        "/wallets/deposit",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def withdraw(client, headers, amount="1.00", key="wd-1"):
    return client.post(
        "/wallets/withdraw",
        json={"amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def transfer(client, headers, to_wallet_id, amount="10.00", key="tr-1"):
    return client.post(
        "/transfers",
        json={"destination_wallet_id": to_wallet_id, "amount": amount},
        headers={**headers, "Idempotency-Key": key},
    )


def listing(client, headers, **params):
    return client.get("/transactions", headers=headers, params=params)


def ids(body) -> list[str]:
    return [item["id"] for item in body["items"]]


# ---------- GET /transactions ----------


def test_new_user_has_an_empty_history(client):
    headers, _ = make_user(client, "alice@example.com")

    response = listing(client, headers)

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_history_is_newest_first_with_direction_and_no_internal_fields(client):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "50.00", "d1")
    withdraw(client, headers, "20.00", "w1")

    items = listing(client, headers).json()["items"]

    assert [i["type"] for i in items] == ["withdrawal", "deposit"]
    assert [i["direction"] for i in items] == ["out", "in"]
    assert items[0]["amount"] == "20.00"
    assert "idempotency_key" not in items[0] and "request_hash" not in items[0]


def test_cursor_walk_returns_every_transaction_once_newest_first(client):
    headers, _ = make_user(client, "alice@example.com")
    created = [deposit(client, headers, "1.00", f"k{i}").json()["id"] for i in range(5)]

    seen, sizes, cursor = [], [], None
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = listing(client, headers, **params).json()
        sizes.append(len(body["items"]))
        seen += ids(body)
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert sizes == [2, 2, 1]
    assert seen == list(reversed(created))


def test_a_new_transaction_between_pages_causes_no_duplicates_or_gaps(client):
    headers, _ = make_user(client, "alice@example.com")
    created = [deposit(client, headers, "1.00", f"k{i}").json()["id"] for i in range(4)]

    page1 = listing(client, headers, limit=2).json()
    late = deposit(client, headers, "1.00", "late").json()["id"]  # arrives mid-walk
    page2 = listing(client, headers, limit=2, cursor=page1["next_cursor"]).json()

    assert ids(page1) == [created[3], created[2]]
    assert ids(page2) == [created[1], created[0]]  # no repeats, nothing skipped
    assert late not in ids(page1) + ids(page2)


def test_rows_with_identical_timestamps_are_neither_skipped_nor_repeated(client, engine):
    headers, wallet_id = make_user(client, "alice@example.com")
    with engine.begin() as conn:
        user_id = str(conn.scalar(text("SELECT id FROM users")))
        for i in range(5):
            conn.execute(
                text(
                    "INSERT INTO transactions (id, user_id, type, status, amount, "
                    "currency, destination_wallet_id, idempotency_key, request_hash, "
                    "created_at) VALUES (gen_random_uuid(), CAST(:uid AS uuid), "
                    "'deposit', 'completed', 1.00, 'INR', CAST(:wid AS uuid), :key, "
                    ":hash, TIMESTAMPTZ '2026-01-01 00:00:00+00')"
                ),
                {"uid": user_id, "wid": wallet_id, "key": f"tie-{i}", "hash": "h" * 64},
            )
    with engine.connect() as conn:
        expected = {str(r) for r in conn.scalars(text("SELECT id FROM transactions"))}

    seen, cursor = [], None
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = listing(client, headers, **params).json()
        seen += ids(body)
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5 and set(seen) == expected


def test_a_transfer_shows_up_for_both_sides_but_not_for_outsiders(client):
    alice, _ = make_user(client, "alice@example.com")
    bob, bob_wallet = make_user(client, "bob@example.com")
    carol, _ = make_user(client, "carol@example.com")
    deposit(client, alice, "100.00", "fund")
    transfer(client, alice, bob_wallet, "30.00")

    alice_items = listing(client, alice).json()["items"]
    bob_items = listing(client, bob).json()["items"]

    assert [i["type"] for i in alice_items] == ["transfer", "deposit"]
    assert alice_items[0]["direction"] == "out"
    assert [i["type"] for i in bob_items] == ["transfer"]
    assert bob_items[0]["direction"] == "in"
    assert listing(client, carol).json()["items"] == []


def test_filter_by_type(client):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "50.00", "d1")
    withdraw(client, headers, "20.00", "w1")

    items = listing(client, headers, type="withdrawal").json()["items"]

    assert [i["type"] for i in items] == ["withdrawal"]


def test_filter_by_status(client):
    headers, _ = make_user(client, "alice@example.com")
    deposit(client, headers, "50.00", "d1")

    assert len(listing(client, headers, status="completed").json()["items"]) == 1
    assert listing(client, headers, status="failed").json()["items"] == []


def test_filter_by_date_range(client, engine):
    headers, _ = make_user(client, "alice@example.com")
    old = deposit(client, headers, "1.00", "old").json()["id"]
    recent = deposit(client, headers, "1.00", "recent").json()["id"]
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE transactions SET created_at = TIMESTAMPTZ '2026-01-01 00:00:00+00' "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": old},
        )

    after = listing(client, headers, created_from="2026-06-01T00:00:00Z").json()
    before = listing(client, headers, created_to="2026-06-01T00:00:00Z").json()

    assert ids(after) == [recent]
    assert ids(before) == [old]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"type": "bogus"},
        {"cursor": "garbage"},
    ],
)
def test_invalid_query_parameters_return_422(client, params):
    headers, _ = make_user(client, "alice@example.com")

    response = listing(client, headers, **params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_from_after_to_returns_422(client):
    headers, _ = make_user(client, "alice@example.com")

    response = listing(
        client,
        headers,
        created_from="2026-06-01T00:00:00Z",
        created_to="2026-01-01T00:00:00Z",
    )

    assert response.status_code == 422


def test_history_requires_login(client):
    assert client.get("/transactions").status_code == 401


# ---------- GET /transactions/{id} ----------


def test_get_own_transaction(client):
    headers, _ = make_user(client, "alice@example.com")
    created = deposit(client, headers, "50.00", "d1").json()

    response = client.get(f"/transactions/{created['id']}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["amount"] == "50.00"
    assert body["direction"] == "in"


def test_recipient_can_read_a_received_transfer(client):
    alice, _ = make_user(client, "alice@example.com")
    bob, bob_wallet = make_user(client, "bob@example.com")
    deposit(client, alice, "100.00", "fund")
    sent = transfer(client, alice, bob_wallet, "30.00").json()

    response = client.get(f"/transactions/{sent['id']}", headers=bob)

    assert response.status_code == 200
    assert response.json()["direction"] == "in"


def test_someone_elses_transaction_returns_404(client):
    alice, _ = make_user(client, "alice@example.com")
    bob, _ = make_user(client, "bob@example.com")
    created = deposit(client, alice, "50.00", "d1").json()

    response = client.get(f"/transactions/{created['id']}", headers=bob)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TRANSACTION_NOT_FOUND"


def test_unknown_transaction_id_returns_404(client):
    headers, _ = make_user(client, "alice@example.com")

    response = client.get(f"/transactions/{ZERO_UUID}", headers=headers)

    assert response.status_code == 404


def test_malformed_transaction_id_returns_422(client):
    headers, _ = make_user(client, "alice@example.com")

    response = client.get("/transactions/not-a-uuid", headers=headers)

    assert response.status_code == 422


def test_transaction_detail_requires_login(client):
    assert client.get(f"/transactions/{ZERO_UUID}").status_code == 401
