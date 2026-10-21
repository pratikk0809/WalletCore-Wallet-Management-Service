"""Integration tests for the Phase 6 cleanup: 401 header and removed debug routes."""
import pytest

PASSWORD = "S3cret!pass"


def test_wrong_password_401_has_the_www_authenticate_header(client):
    client.post("/auth/register", json={"email": "alice@example.com", "password": PASSWORD})

    response = client.post(
        "/auth/login", data={"username": "alice@example.com", "password": "wrong-pass1"}
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["X-Request-ID"]  # the other headers are still there


def test_missing_token_401_has_the_www_authenticate_header(client):
    response = client.get("/wallets/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_invalid_token_401_has_the_www_authenticate_header(client):
    response = client.get("/wallets/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", ["/debug/crash", "/debug/domain-error", "/debug/validate"])
def test_debug_routes_no_longer_exist(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_debug_routes_are_not_in_the_api_documentation(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert not [p for p in paths if p.startswith("/debug")]