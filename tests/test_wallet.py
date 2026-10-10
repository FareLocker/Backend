"""Wallet balance on User + ledger recharge entries."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, LedgerEntry, User


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


def _create_user(
    db: Session,
    *,
    email: str = "traveler@example.com",
    balance_cents: int = 0,
) -> User:
    user = User(
        email=email,
        first_name="Ada",
        last_name="Lovelace",
        phone_number=None,
        hashed_password=hash_password("password123"),
        balance_cents=balance_cents,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth_header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_get_wallet_returns_balance(client, db):
    user = _create_user(db, balance_cents=2500)
    response = client.get(f"/users/{user.id}/wallet", headers=_auth_header(user))

    assert response.status_code == 200
    assert response.json() == {
        "user_id": user.id,
        "balance_cents": 2500,
        "currency": "USD",
    }


def test_get_wallet_rejects_other_user(client, db):
    user = _create_user(db)
    other = _create_user(db, email="other@example.com")
    response = client.get(f"/users/{other.id}/wallet", headers=_auth_header(user))

    assert response.status_code == 403
    assert response.json()["detail"] == "Cannot access another user's wallet"


def test_get_wallet_requires_auth(client, db):
    user = _create_user(db)
    response = client.get(f"/users/{user.id}/wallet")
    assert response.status_code == 401


def test_add_funds_credits_balance_and_ledger(client, db):
    user = _create_user(db, balance_cents=100)
    response = client.post(
        f"/users/{user.id}/wallet/add",
        headers=_auth_header(user),
        json={"amount_cents": 500, "note": "test top-up"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == user.id
    assert body["balance_cents"] == 600
    assert body["credited_cents"] == 500
    assert body["currency"] == "USD"
    assert isinstance(body["ledger_entry_id"], int)

    db.refresh(user)
    assert user.balance_cents == 600

    entry = db.scalar(
        select(LedgerEntry).where(LedgerEntry.id == body["ledger_entry_id"])
    )
    assert entry is not None
    assert entry.user_id == user.id
    assert entry.amount_cents == 500
    assert entry.entry_type == "recharge"
    assert entry.note == "test top-up"
    assert entry.lock_id is None


def test_add_funds_rejects_other_user(client, db):
    user = _create_user(db)
    other = _create_user(db, email="other@example.com")
    response = client.post(
        f"/users/{other.id}/wallet/add",
        headers=_auth_header(user),
        json={"amount_cents": 100},
    )

    assert response.status_code == 403
    db.refresh(user)
    db.refresh(other)
    assert user.balance_cents == 0
    assert other.balance_cents == 0
    assert db.scalars(select(LedgerEntry)).all() == []


def test_add_funds_rejects_non_positive_amount(client, db):
    user = _create_user(db)
    response = client.post(
        f"/users/{user.id}/wallet/add",
        headers=_auth_header(user),
        json={"amount_cents": 0},
    )
    assert response.status_code == 422
