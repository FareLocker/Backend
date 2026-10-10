"""Early exercise of open FareLocks (server fetches current fare)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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


def _open_lock(
    db: Session,
    user: User,
    *,
    strike: float = 100.0,
    fee: float = 15.0,
    departure: date | None = None,
    status: str = "open",
) -> Lock:
    lock = Lock(
        user_id=user.id,
        route="ATL-JFK",
        flight_no="DL123",
        departure_date=departure or (date.today() + timedelta(days=10)),
        K=strike,
        T_days=10,
        d0=10,
        model_value=10.0,
        fee=fee,
        lam=0.3,
        sigma_used=0.35,
        status=status,
        bought_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)
    return lock


def _stub_current_fare(
    monkeypatch, dollars: float | None, *, source: str = "provider"
) -> None:
    if dollars is None:
        monkeypatch.setattr(
            locks_router, "_resolve_current_fare", lambda _lock, _db: (None, None)
        )
        return
    monkeypatch.setattr(
        locks_router,
        "_resolve_current_fare",
        lambda _lock, _db: (dollars, source),
    )


def test_early_exercise_credits_payout_when_fare_below_strike(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 80.0)
    user = _create_user(db, balance_cents=500)
    lock = _open_lock(db, user, strike=100.0, fee=15.0)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == lock.id
    assert body["status"] == "exercised"
    assert body["strike_cents"] == 10000
    assert body["settle_fare_cents"] == 8000
    assert body["payout_cents"] == 2000
    assert body["fee_cents"] == 1500
    assert body["balance_cents"] == 2500
    assert body["currency"] == "USD"

    db.refresh(user)
    db.refresh(lock)
    assert user.balance_cents == 2500
    assert lock.status == "exercised"
    assert lock.settle_fare == 80.0
    assert lock.payout == 20.0
    assert lock.settled_at is not None

    entry = db.scalar(
        select(LedgerEntry).where(
            LedgerEntry.lock_id == lock.id, LedgerEntry.entry_type == "payout"
        )
    )
    assert entry is not None
    assert entry.amount_cents == 2000


def test_early_exercise_allows_payout_smaller_than_fee(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 95.0)
    user = _create_user(db, balance_cents=0)
    lock = _open_lock(db, user, strike=100.0, fee=15.0)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["payout_cents"] == 500
    assert body["fee_cents"] == 1500
    assert body["balance_cents"] == 500


def test_early_exercise_caps_payout_at_half_strike(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 1.0)
    user = _create_user(db, balance_cents=0)
    lock = _open_lock(db, user, strike=100.0)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    assert response.json()["payout_cents"] == 5000


def test_early_exercise_rejects_fare_at_or_above_strike(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 100.0)
    user = _create_user(db)
    lock = _open_lock(db, user, strike=100.0)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "fare_not_below_strike"
    db.refresh(lock)
    assert lock.status == "open"


def test_early_exercise_falls_back_to_stored_fare(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 85.0, source="stored")
    user = _create_user(db, balance_cents=0)
    lock = _open_lock(db, user, strike=100.0)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 200
    assert response.json()["settle_fare_cents"] == 8500
    assert response.json()["payout_cents"] == 1500


def test_early_exercise_rejects_when_fare_unavailable(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, None)
    user = _create_user(db)
    lock = _open_lock(db, user)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "fare_unavailable"


def test_early_exercise_rejects_after_departure(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 50.0)
    user = _create_user(db)
    lock = _open_lock(db, user, departure=date.today())

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "past_exercise_window"


def test_early_exercise_rejects_non_open_lock(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 50.0)
    user = _create_user(db)
    lock = _open_lock(db, user, status="expired")

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(user),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "lock_not_open"


def test_early_exercise_rejects_other_users_lock(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 50.0)
    owner = _create_user(db, email="owner@example.com")
    other = _create_user(db, email="other@example.com")
    lock = _open_lock(db, owner)

    response = client.post(
        f"/locks/{lock.id}/exercise",
        headers=_auth_header(other),
    )

    assert response.status_code == 403
    db.refresh(lock)
    assert lock.status == "open"


def test_early_exercise_requires_auth(client, db):
    user = _create_user(db)
    lock = _open_lock(db, user)

    response = client.post(f"/locks/{lock.id}/exercise")
    assert response.status_code == 401


def test_early_exercise_not_found(client, db, monkeypatch):
    _stub_current_fare(monkeypatch, 50.0)
    user = _create_user(db)
    response = client.post(
        "/locks/99999/exercise",
        headers=_auth_header(user),
    )
    assert response.status_code == 404
