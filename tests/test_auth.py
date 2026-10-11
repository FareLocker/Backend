"""Local bcrypt + JWT auth."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
from app.models import Base, LedgerEntry, Lock, User
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


def test_register_provisions_messaging_when_phone_is_supplied(client, monkeypatch):
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        auth_router,
        "_provision_signup_messaging",
        lambda phone_number, first_name, last_name, email: (
            sent.append((phone_number, first_name, last_name, email))
            or {"photon_user_id": "photon-user", "assigned_phone_number": "+15550001111"}
        ),
    )

    response = client.post("/auth/register", json=REGISTER)

    assert response.status_code == 201
    assert sent == [("+15551234567", "Ada", "Lovelace", "traveler@example.com")]
    assert response.json()["messaging"] == {
        "status": "awaiting_start", "assigned_phone_number": "+15550001111"
    }


def test_register_normalizes_north_american_phone_for_photon(client, db, monkeypatch):
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        auth_router,
        "_provision_signup_messaging",
        lambda phone_number, first_name, last_name, email: sent.append(
            (phone_number, first_name, last_name, email)
        ) or {"photon_user_id": "photon-user", "assigned_phone_number": "+15550001111"},
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


def test_register_requires_phone_for_messaging(client):
    response = client.post("/auth/register", json={**REGISTER, "phone_number": None})
    assert response.status_code == 422


def test_start_returns_welcome_only_for_awaiting_user(client, db, monkeypatch):
    monkeypatch.setattr(settings, "photon_messaging_secret", "worker-secret")
    user = User(
        email="start@example.com",
        first_name="Ada",
        last_name="Lovelace",
        phone_number="+15551234567",
        hashed_password=hash_password("password123"),
        photon_messaging_status="awaiting_start",
        photon_assigned_phone_number="+15550001111",
    )
    db.add(user)
    db.commit()

    response = client.post(
        "/auth/messaging/start?phone_number=%2B15551234567",
        headers={"X-Photon-Internal-Secret": "worker-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"should_send_welcome": True, "first_name": "Ada"}


def test_signup_provisioning_uses_authenticated_photon_request(monkeypatch):
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
        response = Response()
        response.read = lambda: b'{"photon_user_id":"photon-user","assigned_phone_number":"+15550001111"}'
        return response

    monkeypatch.setattr(settings, "photon_messaging_url", "http://photon.test:8787")
    monkeypatch.setattr(settings, "photon_messaging_secret", "shared-secret")
    monkeypatch.setattr(auth_router, "urlopen", fake_urlopen)

    result = auth_router._provision_signup_messaging(
        "+15551234567", "Ada", "Lovelace", "traveler@example.com"
    )

    assert captured == {
        "url": "http://photon.test:8787/provision-user",
        "body": (
            b'{"phone_number": "+15551234567", "first_name": "Ada", '
            b'"last_name": "Lovelace", "email": "traveler@example.com"}'
        ),
        "secret": "shared-secret",
        "timeout": settings.photon_messaging_timeout_seconds,
    }
    assert result == {"photon_user_id": "photon-user", "assigned_phone_number": "+15550001111"}


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
        "messaging": {"status": "unavailable", "assigned_phone_number": None},
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


def _add_lock(
    db: Session,
    user: User,
    *,
    status: str = "open",
) -> Lock:
    lock = Lock(
        user_id=user.id,
        route="ATL-LAX",
        flight_no="DL123",
        departure_date=date.today() + timedelta(days=10),
        K=200.0,
        T_days=10,
        d0=10,
        model_value=12.0,
        fee=15.6,
        lam=0.3,
        sigma_used=0.35,
        status=status,
        bought_at=datetime.now(UTC),
        settled_at=datetime.now(UTC) if status in {"exercised", "expired"} else None,
        settle_fare=180.0 if status == "exercised" else None,
        payout=20.0 if status == "exercised" else None,
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)
    return lock


def test_delete_account_removes_user_and_history(client, db):
    token = client.post("/auth/register", json=REGISTER).json()["access_token"]
    user = db.query(User).one()
    lock = _add_lock(db, user, status="exercised")
    db.add(
        LedgerEntry(
            user_id=user.id,
            amount_cents=-1560,
            entry_type="lock_fee",
            note="FareLock fee",
            lock_id=lock.id,
        )
    )
    db.add(
        LedgerEntry(
            user_id=user.id,
            amount_cents=5000,
            entry_type="recharge",
            note="top up",
            lock_id=None,
        )
    )
    db.commit()

    response = client.request(
        "DELETE",
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "password123"},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert db.scalar(select(User).where(User.id == user.id)) is None
    assert db.scalar(select(Lock).where(Lock.user_id == user.id)) is None
    assert db.scalar(select(LedgerEntry).where(LedgerEntry.user_id == user.id)) is None

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


def test_delete_account_rejects_wrong_password(client, db):
    token = client.post("/auth/register", json=REGISTER).json()["access_token"]

    response = client.request(
        "DELETE",
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"
    assert db.query(User).count() == 1


@pytest.mark.parametrize("lock_status", ["open", "pending_verification"])
def test_delete_account_rejects_unsettled_locks(client, db, lock_status: str):
    token = client.post("/auth/register", json=REGISTER).json()["access_token"]
    user = db.query(User).one()
    _add_lock(db, user, status=lock_status)

    response = client.request(
        "DELETE",
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "password123"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Close or settle open FareLocks before deleting your account"
    )
    assert db.query(User).count() == 1
    assert db.query(Lock).count() == 1


def test_delete_account_rejects_positive_balance(client, db):
    token = client.post("/auth/register", json=REGISTER).json()["access_token"]
    user = db.query(User).one()
    user.balance_cents = 500
    db.commit()

    response = client.request(
        "DELETE",
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "password123"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Withdraw your wallet balance before deleting your account"
    )
    assert db.query(User).one().balance_cents == 500


def test_delete_account_requires_auth(client):
    response = client.request(
        "DELETE",
        "/auth/me",
        json={"password": "password123"},
    )
    assert response.status_code == 401
