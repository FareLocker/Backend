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


def test_get_transactions_empty(client, db):
    user = _create_user(db)
    response = client.get(
        f"/users/{user.id}/wallet/transactions",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": user.id, "transactions": []}


def test_get_transactions_returns_newest_first(client, db):
    user = _create_user(db)
    older = LedgerEntry(
        user_id=user.id,
        amount_cents=100,
        entry_type="recharge",
        note="first",
        lock_id=None,
    )
    newer = LedgerEntry(
        user_id=user.id,
        amount_cents=-50,
        entry_type="lock_fee",
        note="fee",
        lock_id=None,
    )
    db.add_all([older, newer])
    db.commit()
    db.refresh(older)
    db.refresh(newer)

    response = client.get(
        f"/users/{user.id}/wallet/transactions",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == user.id
    assert [tx["id"] for tx in body["transactions"]] == [newer.id, older.id]
    assert body["transactions"][0]["amount_cents"] == -50
    assert body["transactions"][0]["entry_type"] == "lock_fee"
    assert body["transactions"][0]["note"] == "fee"
    assert body["transactions"][0]["lock_id"] is None
    assert "created_at" in body["transactions"][0]
    assert body["transactions"][1]["amount_cents"] == 100
    assert body["transactions"][1]["entry_type"] == "recharge"


def test_get_transactions_excludes_other_users(client, db):
    user = _create_user(db)
    other = _create_user(db, email="other@example.com")
    db.add(
        LedgerEntry(
            user_id=other.id,
            amount_cents=999,
            entry_type="recharge",
            note="secret",
            lock_id=None,
        )
    )
    db.add(
        LedgerEntry(
            user_id=user.id,
            amount_cents=25,
            entry_type="recharge",
            note="mine",
            lock_id=None,
        )
    )
    db.commit()

    response = client.get(
        f"/users/{user.id}/wallet/transactions",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    txs = response.json()["transactions"]
    assert len(txs) == 1
    assert txs[0]["amount_cents"] == 25
    assert txs[0]["note"] == "mine"


def test_get_transactions_rejects_other_user(client, db):
    user = _create_user(db)
    other = _create_user(db, email="other@example.com")
    response = client.get(
        f"/users/{other.id}/wallet/transactions",
        headers=_auth_header(user),
    )

    assert response.status_code == 403


def test_get_transactions_requires_auth(client, db):
    user = _create_user(db)
    response = client.get(f"/users/{user.id}/wallet/transactions")
    assert response.status_code == 401


def test_get_transactions_filters_by_entry_type(client, db):
    user = _create_user(db)
    db.add_all(
        [
            LedgerEntry(
                user_id=user.id,
                amount_cents=100,
                entry_type="recharge",
                note="top-up",
                lock_id=None,
            ),
            LedgerEntry(
                user_id=user.id,
                amount_cents=-40,
                entry_type="lock_fee",
                note="fee",
                lock_id=None,
            ),
            LedgerEntry(
                user_id=user.id,
                amount_cents=200,
                entry_type="recharge",
                note="another",
                lock_id=None,
            ),
        ]
    )
    db.commit()

    response = client.get(
        f"/users/{user.id}/wallet/transactions",
        headers=_auth_header(user),
        params={"entry_type": "recharge"},
    )

    assert response.status_code == 200
    txs = response.json()["transactions"]
    assert len(txs) == 2
    assert {tx["entry_type"] for tx in txs} == {"recharge"}
    assert {tx["amount_cents"] for tx in txs} == {100, 200}


def test_get_transactions_filter_lock_fee_only(client, db):
    user = _create_user(db)
    db.add_all(
        [
            LedgerEntry(
                user_id=user.id,
                amount_cents=100,
                entry_type="recharge",
                note=None,
                lock_id=None,
            ),
            LedgerEntry(
                user_id=user.id,
                amount_cents=-40,
                entry_type="lock_fee",
                note="fee",
                lock_id=None,
            ),
        ]
    )
    db.commit()

    response = client.get(
        f"/users/{user.id}/wallet/transactions",
        headers=_auth_header(user),
        params={"entry_type": "lock_fee"},
    )

    assert response.status_code == 200
    txs = response.json()["transactions"]
    assert len(txs) == 1
    assert txs[0]["entry_type"] == "lock_fee"
    assert txs[0]["amount_cents"] == -40


def test_get_transactions_rejects_invalid_entry_type(client, db):
    user = _create_user(db)
    response = client.get(
        f"/users/{user.id}/wallet/transactions",
        headers=_auth_header(user),
        params={"entry_type": "not_a_real_type"},
    )
    assert response.status_code == 422
