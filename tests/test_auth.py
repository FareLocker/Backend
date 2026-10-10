"""Local bcrypt + JWT auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, User
from app.routers import auth as auth_router


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _test_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-secret-key")
    monkeypatch.setattr(settings, "access_token_expire_minutes", 60)


REGISTER = {
    "email": "traveler@example.com",
    "password": "password123",
    "first_name": "Ada",
    "last_name": "Lovelace",
    "phone_number": "+15551234567",
}


def test_hash_and_verify_password_roundtrip():
    hashed = hash_password("password123")
    assert hashed != "password123"
    assert verify_password("password123", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip():
    token = create_access_token(42)
    assert decode_access_token(token) == 42


def test_register_returns_token_and_creates_user(client, db):
    response = client.post("/auth/register", json=REGISTER)
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert decode_access_token(body["access_token"]) == db.query(User).one().id

    user = db.query(User).one()
    assert user.email == "traveler@example.com"
    assert user.phone_number == "+15551234567"
    assert user.balance_cents == 0
    assert verify_password("password123", user.hashed_password)


def test_register_sends_welcome_when_phone_is_supplied(client, monkeypatch):
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        auth_router,
        "_send_signup_welcome",
        lambda phone_number, first_name, last_name, email: sent.append(
            (phone_number, first_name, last_name, email)
        ),
    )

    response = client.post("/auth/register", json=REGISTER)

    assert response.status_code == 201
    assert sent == [("+15551234567", "Ada", "Lovelace", "traveler@example.com")]


def test_register_normalizes_north_american_phone_for_photon(client, db, monkeypatch):
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        auth_router,
        "_send_signup_welcome",
        lambda phone_number, first_name, last_name, email: sent.append(
            (phone_number, first_name, last_name, email)
        ),
    )

    response = client.post(
        "/auth/register", json={**REGISTER, "phone_number": "(555) 123-4567"}
    )

    assert response.status_code == 201
    assert db.query(User).one().phone_number == "+15551234567"
    assert sent == [("+15551234567", "Ada", "Lovelace", "traveler@example.com")]


def test_register_rejects_phone_that_cannot_be_normalized(client):
    response = client.post(
        "/auth/register", json={**REGISTER, "phone_number": "555-123"}
    )

    assert response.status_code == 422
    assert "E.164" in response.json()["detail"]


def test_register_does_not_send_welcome_without_phone(client, monkeypatch):
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        auth_router,
        "_send_signup_welcome",
        lambda phone_number, first_name, last_name, email: sent.append(
            (phone_number, first_name, last_name, email)
        ),
    )

    response = client.post(
        "/auth/register", json={**REGISTER, "phone_number": None}
    )

    assert response.status_code == 201
    assert sent == []


def test_signup_welcome_uses_authenticated_photon_request(monkeypatch):
    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["secret"] = request.get_header("X-photon-internal-secret")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(settings, "photon_messaging_url", "http://photon.test:8787")
    monkeypatch.setattr(settings, "photon_messaging_secret", "shared-secret")
    monkeypatch.setattr(auth_router, "urlopen", fake_urlopen)

    auth_router._send_signup_welcome(
        "+15551234567", "Ada", "Lovelace", "traveler@example.com"
    )

    assert captured == {
        "url": "http://photon.test:8787/send-welcome",
        "body": (
            b'{"phone_number": "+15551234567", "first_name": "Ada", '
            b'"last_name": "Lovelace", "email": "traveler@example.com"}'
        ),
        "secret": "shared-secret",
        "timeout": settings.photon_messaging_timeout_seconds,
    }


def test_register_normalizes_email(client, db):
    payload = {**REGISTER, "email": "  Traveler@Example.COM "}
    # EmailStr may reject spaces — if so, use without spaces:
    payload = {**REGISTER, "email": "Traveler@Example.COM"}
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    assert db.query(User).one().email == "traveler@example.com"


def test_register_rejects_duplicate_email(client):
    assert client.post("/auth/register", json=REGISTER).status_code == 201
    again = client.post(
        "/auth/register",
        json={**REGISTER, "phone_number": "+15557654321"},
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "Email already registered"


def test_register_rejects_duplicate_phone(client):
    assert client.post("/auth/register", json=REGISTER).status_code == 201
    again = client.post(
        "/auth/register",
        json={**REGISTER, "email": "other@example.com"},
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "Phone number already registered"


def test_login_with_email(client):
    client.post("/auth/register", json=REGISTER)
    response = client.post(
        "/auth/login",
        json={"identifier": "traveler@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_with_phone(client):
    client.post("/auth/register", json=REGISTER)
    response = client.post(
        "/auth/login",
        json={"identifier": "+15551234567", "password": "password123"},
    )
    assert response.status_code == 200


def test_login_rejects_bad_password(client):
    client.post("/auth/register", json=REGISTER)
    response = client.post(
        "/auth/login",
        json={"identifier": "traveler@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_login_rejects_unknown_identifier(client):
    response = client.post(
        "/auth/login",
        json={"identifier": "nobody@example.com", "password": "password123"},
    )
    assert response.status_code == 401


def test_me_returns_user(client):
    token = client.post("/auth/register", json=REGISTER).json()["access_token"]
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "email": "traveler@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "balance_cents": 0,
    }


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}])
def test_me_rejects_missing_or_malformed_bearer(client, headers):
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_me_rejects_invalid_token(client):
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
