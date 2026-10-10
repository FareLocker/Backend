"""Purchase FareLock endpoints: debit wallet + create Lock."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, LedgerEntry, Lock, User
from app.routers import locks as locks_router


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
    balance_cents: int = 50_000,
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


def _fake_quote(*, fee: float = 12.5, k: float = 420.0, t_days: int = 10):
    return SimpleNamespace(
        K=k,
        fee=fee,
        V_model=fee / 1.3,
        lam=0.3,
        sigma_used=0.35,
        T_days=t_days,
    )


@pytest.fixture
def mock_price(monkeypatch):
    pricing = MagicMock(return_value=_fake_quote())
    monkeypatch.setattr(locks_router, "_price_farelock", pricing)
    return pricing


def test_purchase_structured_creates_lock_and_debits_wallet(
    client, db, mock_price
):
    user = _create_user(db, balance_cents=10_000)
    departure = date.today() + timedelta(days=10)

    response = client.post(
        "/locks/farelock/purchase",
        headers=_auth_header(user),
        json={
            "origin": "atl",
            "destination": "lax",
            "departure_date": departure.isoformat(),
            "flight_number": "dl 123",
            "fare_cents": 42000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == "ATL"
    assert body["destination"] == "LAX"
    assert body["flight_number"] == "DL123"
    assert body["strike_cents"] == 42000
    assert body["fee_cents"] == 1250
    assert body["max_payout_cents"] == 21000
    assert body["protection_days"] == 10
    assert body["status"] == "open"
    assert body["balance_cents"] == 8750
    assert body["currency"] == "USD"

    db.refresh(user)
    assert user.balance_cents == 8750

    lock = db.get(Lock, body["id"])
    assert lock is not None
    assert lock.user_id == user.id
    assert lock.route == "ATL-LAX"
    assert lock.flight_no == "DL123"
    assert lock.K == 420.0
    assert lock.fee == 12.5
    assert lock.status == "open"

    entry = db.scalar(
        select(LedgerEntry).where(LedgerEntry.lock_id == lock.id)
    )
    assert entry is not None
    assert entry.entry_type == "lock_fee"
    assert entry.amount_cents == -1250
    assert entry.user_id == user.id

    mock_price.assert_called_once()
    kwargs = mock_price.call_args.kwargs
    assert kwargs["origin"] == "ATL"
    assert kwargs["destination"] == "LAX"
    assert kwargs["flight_no"] == "DL123"
    assert kwargs["s0_dollars"] == 420.0


def test_purchase_natural_language(client, db, mock_price, monkeypatch):
    user = _create_user(db, balance_cents=10_000)
    departure = date.today() + timedelta(days=10)

    parsed = SimpleNamespace(
        origin="ATL",
        destination="LAX",
        departure_date=departure,
        flight_number="DL 123",
        fare_paid=420.0,
    )
    monkeypatch.setattr(
        locks_router, "parse_natural_farelock_quote", lambda _q: parsed
    )

    response = client.post(
        "/locks/farelock",
        headers=_auth_header(user),
        json={
            "query": f"I booked ATL to LAX on {departure.isoformat()} for $420"
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fee_cents"] == 1250
    assert body["balance_cents"] == 8750
    assert db.get(Lock, body["id"]) is not None


def test_purchase_rejects_insufficient_funds(client, db, mock_price):
    user = _create_user(db, balance_cents=100)
    departure = date.today() + timedelta(days=10)

    response = client.post(
        "/locks/farelock/purchase",
        headers=_auth_header(user),
        json={
            "origin": "ATL",
            "destination": "LAX",
            "departure_date": departure.isoformat(),
            "fare_cents": 42000,
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_funds"
    assert detail["balance_cents"] == 100
    assert detail["fee_cents"] == 1250

    db.refresh(user)
    assert user.balance_cents == 100
    assert db.scalars(select(Lock)).all() == []
    assert db.scalars(select(LedgerEntry)).all() == []


def test_purchase_requires_auth(client, db):
    departure = date.today() + timedelta(days=10)
    response = client.post(
        "/locks/farelock/purchase",
        json={
            "origin": "ATL",
            "destination": "LAX",
            "departure_date": departure.isoformat(),
            "fare_cents": 42000,
        },
    )
    assert response.status_code == 401
