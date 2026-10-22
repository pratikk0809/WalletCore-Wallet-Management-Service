"""The generated OpenAPI schema is complete, and matches real behavior."""
import pytest


@pytest.fixture
def spec(client):
    return client.get("/openapi.json").json()


def operations(spec):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            yield path, method, operation


def test_every_operation_has_a_tag_and_a_description(spec):
    for path, method, op in operations(spec):
        assert op.get("tags"), f"{method.upper()} {path} has no tag"
        assert op.get("description"), f"{method.upper()} {path} has no description"


def test_every_tag_in_use_has_a_description(spec):
    described = {t["name"] for t in spec["tags"] if t.get("description")}
    used = {tag for _, _, op in operations(spec) for tag in op["tags"]}

    assert used <= described


def test_every_protected_operation_documents_401(spec):
    for path, method, op in operations(spec):
        if path in {"/health", "/auth/register"}:
            continue
        assert "401" in op["responses"], f"{method.upper()} {path} does not document 401"


@pytest.mark.parametrize(
    "path, extra",
    [
        ("/wallets/deposit", {"403"}),
        ("/wallets/withdraw", {"403"}),
        ("/transfers", {"403", "404"}),
    ],
)
def test_money_endpoints_document_every_outcome(spec, path, extra):
    documented = set(spec["paths"][path]["post"]["responses"])

    assert {"200", "201", "401", "409", "422", "429"} | extra <= documented


def test_422_is_documented_in_our_error_format_not_the_framework_default(spec):
    for path, method, op in operations(spec):
        if "422" in op["responses"]:
            schema = op["responses"]["422"]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, f"{method} {path}"


def test_429_documents_retry_after(spec):
    headers = spec["paths"]["/auth/login"]["post"]["responses"]["429"]["headers"]

    assert "Retry-After" in headers


def test_the_idempotency_header_is_documented_and_required(spec):
    params = spec["paths"]["/wallets/deposit"]["post"]["parameters"]
    key = next(p for p in params if p["name"].lower() == "idempotency-key")

    assert key["in"] == "header" and key["required"] is True and key["description"]


def test_the_admin_endpoint_documents_403(spec):
    responses = spec["paths"]["/admin/wallets/{wallet_id}/freeze"]["post"]["responses"]

    assert "403" in responses


def test_request_bodies_have_examples(spec):
    schemas = spec["components"]["schemas"]

    assert schemas["DepositRequest"]["properties"]["amount"]["examples"] == ["250.75"]
    assert schemas["TransferRequest"]["properties"]["destination_wallet_id"]["examples"]
    assert schemas["RegisterRequest"]["properties"]["email"]["examples"] == ["alice@example.com"]


def test_the_documented_401_example_matches_a_real_401(client, spec):
    example = spec["paths"]["/wallets/me"]["get"]["responses"]["401"]["content"][
        "application/json"
    ]["example"]

    real = client.get("/wallets/me").json()

    assert set(real["error"]) == set(example["error"])
    assert real["error"]["code"] == example["error"]["code"]


def test_the_documented_422_example_matches_a_real_422(client, spec):
    example = spec["paths"]["/auth/register"]["post"]["responses"]["422"]["content"][
        "application/json"
    ]["example"]

    real = client.post("/auth/register", json={"email": "bad", "password": "x"}).json()

    assert set(real["error"]) == set(example["error"])  # code, message, request_id, details
    assert set(real["error"]["details"][0]) == set(example["error"]["details"][0])


def test_the_api_has_a_version_and_a_description(spec):
    assert spec["info"]["version"] == "1.0.0"
    assert "Idempotency" in spec["info"]["description"]