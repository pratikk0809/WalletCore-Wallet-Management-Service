"""Unit tests for the docs switch (no database)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.openapi import docs_urls


def test_docs_are_on_locally():
    assert docs_urls("local") == {
        "docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json",
    }


@pytest.mark.parametrize("environment", ["production", "staging", ""])
def test_docs_are_off_everywhere_else(environment):
    assert docs_urls(environment) == {
        "docs_url": None, "redoc_url": None, "openapi_url": None,
    }


def test_a_production_app_serves_no_docs_and_no_schema():
    client = TestClient(FastAPI(**docs_urls("production")))

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_a_local_app_serves_docs_and_schema():
    client = TestClient(FastAPI(**docs_urls("local")))

    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200