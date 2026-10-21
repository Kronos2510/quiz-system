"""Test configuration. Environment must be set before importing the app."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_DIR = Path(tempfile.mkdtemp(prefix="quizsystem-tests-"))
DB_PATH = (TEST_DIR / "test.db").as_posix()

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["DIRECT_URL"] = f"sqlite:///{DB_PATH}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "AdminTest123!"
os.environ["ADMIN_NAME"] = "Test Admin"
os.environ["COOKIE_SECURE"] = "0"
os.environ["SEED_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from quizapp.db import SessionLocal  # noqa: E402
from quizapp.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def second_client():
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def admin_email() -> str:
    return os.environ["ADMIN_EMAIL"]


@pytest.fixture(scope="session")
def admin_password() -> str:
    return os.environ["ADMIN_PASSWORD"]
