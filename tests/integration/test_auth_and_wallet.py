"""Integration tests: real HTTP calls against the app with a real PostgreSQL database."""

import uuid
from datetime import timedelta

from sqlalchemy import text

from app.core.security import create_access_token

EMAIL = "alice@example.com"
PASSWORD = "S3cret!pass"


def register(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password})


def auth_headers(client, email=EMAIL, password=PASSWORD) -> dict[str, str]:
    """Register + login, and return the Authorization header."""
    register(client, email, password)
    response = client.post("/auth/login", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------- register ----------


def test_register_returns_201_and_hides_password(client):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == EMAIL
    assert "password" not in body and "password_hash" not in body


def test_register_duplicate_email_returns_409(client):
    register(client)
    response = register(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_register_weak_password_returns_422(client):
    response = register(client, password="short")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "password"


# ---------- login ----------


def test_login_failures_are_identical_for_wrong_password_and_unknown_email(client):
    register(client)

    wrong_pw = client.post("/auth/login", data={"username": EMAIL, "password": "wrong-pass1"})
    unknown = client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": "wrong-pass1"}
    )

    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_login_email_is_case_insensitive(client):
    register(client)
    response = client.post(
        "/auth/login", data={"username": "ALICE@Example.com", "password": PASSWORD}
    )
    assert response.status_code == 200


# ---------- GET /wallets/me ----------


def test_wallet_me_new_user_has_empty_active_wallet(client):
    response = client.get("/wallets/me", headers=auth_headers(client))

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == "0.00"
    assert body["currency"] == "INR"
    assert body["status"] == "active"


def test_wallet_me_without_token_returns_401(client):
    response = client.get("/wallets/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_wallet_me_with_garbage_token_returns_401(client):
    response = client.get("/wallets/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_wallet_me_with_expired_token_returns_401(client):
    register(client)
    token = create_access_token(str(uuid.uuid4()), expires_delta=timedelta(seconds=-1))

    response = client.get("/wallets/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_wallet_me_with_token_for_unknown_user_returns_401(client):
    token = create_access_token(str(uuid.uuid4()))  # valid signature, but no such user

    response = client.get("/wallets/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_wallet_me_for_disabled_user_returns_401(client, engine):
    headers = auth_headers(client)
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET status = 'disabled'"))

    response = client.get("/wallets/me", headers=headers)
    assert response.status_code == 401


def test_each_user_sees_only_their_own_wallet(client):
    alice = client.get("/wallets/me", headers=auth_headers(client, "alice@example.com")).json()
    bob = client.get("/wallets/me", headers=auth_headers(client, "bob@example.com")).json()

    assert alice["id"] != bob["id"]
