"""Fixtures for integration tests.

They use a SEPARATE database (<your db name>_test) in the same Postgres server,
so your development data is never touched.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.config import get_settings
from app.main import app
from app.models import Base

settings = get_settings()
_dev_url = make_url(settings.database_url)
TEST_DB_NAME = f"{_dev_url.database}_test"
_test_url = _dev_url.set(database=TEST_DB_NAME)


def _create_test_database_if_missing() -> None:
    # CREATE DATABASE can't run inside a transaction, hence AUTOCOMMIT.
    admin_engine = create_engine(_dev_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": TEST_DB_NAME},
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def engine():
    """Create the test DB and all tables once per test run."""
    _create_test_database_if_missing()
    test_engine = create_engine(_test_url, pool_pre_ping=True)
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def client(engine):
    """A test HTTP client wired to the test DB, with all tables emptied first."""
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Empty every table (new tables are picked up automatically).
    table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()