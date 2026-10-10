"""List and get FareLock endpoints."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, Lock, User


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


def _add_lock(
    db: Session,
    user: User,
    *,
    status: str = "open",
    bought_at: datetime | None = None,
    settle_fare: float | None = None,
    payout: float | None = None,
    settled_at: datetime | None = None,
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
        bought_at=bought_at or datetime.now(UTC),
        settled_at=settled_at,
        settle_fare=settle_fare,
        payout=payout,
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)
    return lock


def test_list_locks_empty(client, db):
    user = _create_user(db)
    response = client.get("/locks", headers=_auth_header(user))

    assert response.status_code == 200
    assert response.json() == {"locks": []}


def test_list_locks_returns_own_newest_first(client, db):
    user = _create_user(db)
    other = _create_user(db, email="other@example.com")
    older = _add_lock(
        db, user, bought_at=datetime(2026, 10, 1, 12, tzinfo=UTC)
    )
    newer = _add_lock(
        db, user, bought_at=datetime(2026, 10, 5, 12, tzinfo=UTC)
    )
    _add_lock(db, other)

    response = client.get("/locks", headers=_auth_header(user))

    assert response.status_code == 200
    locks = response.json()["locks"]
    assert [lock["id"] for lock in locks] == [newer.id, older.id]
    assert locks[0]["origin"] == "ATL"
    assert locks[0]["destination"] == "LAX"
    assert locks[0]["flight_number"] == "DL123"
    assert locks[0]["strike_cents"] == 20000
    assert locks[0]["fee_cents"] == 1560
    assert locks[0]["max_payout_cents"] == 10000
    assert locks[0]["status"] == "open"
    assert locks[0]["settle_fare_cents"] is None
    assert locks[0]["payout_cents"] is None
    assert locks[0]["currency"] == "USD"


def test_list_locks_filters_by_status(client, db):
    user = _create_user(db)
    open_lock = _add_lock(db, user, status="open")
    _add_lock(
        db,
        user,
        status="exercised",
        settle_fare=150.0,
        payout=50.0,
        settled_at=datetime.now(UTC),
    )

    response = client.get(
        "/locks",
        headers=_auth_header(user),
        params={"status": "open"},
    )

    assert response.status_code == 200
    locks = response.json()["locks"]
    assert len(locks) == 1
    assert locks[0]["id"] == open_lock.id
    assert locks[0]["status"] == "open"


def test_list_locks_rejects_invalid_status(client, db):
    user = _create_user(db)
    response = client.get(
        "/locks",
        headers=_auth_header(user),
        params={"status": "not_a_status"},
    )
    assert response.status_code == 422


def test_list_locks_requires_auth(client, db):
    assert client.get("/locks").status_code == 401


def test_get_lock_returns_detail(client, db):
    user = _create_user(db)
    lock = _add_lock(
        db,
        user,
        status="exercised",
        settle_fare=180.0,
        payout=20.0,
        settled_at=datetime(2026, 10, 8, 15, tzinfo=UTC),
    )

    response = client.get(f"/locks/{lock.id}", headers=_auth_header(user))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == lock.id
    assert body["status"] == "exercised"
    assert body["settle_fare_cents"] == 18000
    assert body["payout_cents"] == 2000
    assert body["settled_at"] is not None


def test_get_lock_rejects_other_user(client, db):
    owner = _create_user(db, email="owner@example.com")
    other = _create_user(db, email="other@example.com")
    lock = _add_lock(db, owner)

    response = client.get(f"/locks/{lock.id}", headers=_auth_header(other))

    assert response.status_code == 403


def test_get_lock_not_found(client, db):
    user = _create_user(db)
    response = client.get("/locks/99999", headers=_auth_header(user))
    assert response.status_code == 404


def test_get_lock_requires_auth(client, db):
    user = _create_user(db)
    lock = _add_lock(db, user)
    assert client.get(f"/locks/{lock.id}").status_code == 401
