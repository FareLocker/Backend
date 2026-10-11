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
from app.models import Base, FareSnapshot, LedgerEntry, Lock, User
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


def _add_fare_snapshot(db: Session, departure: date) -> FareSnapshot:
    fare_key = f"atl-lax-{departure.isoformat()}-dl123"
    snapshot = FareSnapshot(
        id=fare_key,
        payload={
            "id": fare_key,
            "airline_name": "Delta",
            "flight_number": "DL123",
            "cabin_class": "Economy",
            "stop_airports": [],
            "depart": {
                "airport_code": "ATL",
                "airport_name": "Hartsfield-Jackson Atlanta International",
                "city": "ATL",
                "local_time": f"{departure.isoformat()}T08:00",
            },
            "arrive": {
                "airport_code": "LAX",
                "airport_name": "Los Angeles International",
                "city": "LAX",
                "local_time": f"{departure.isoformat()}T10:00",
            },
            "duration_minutes": 300,
            "fare_cents": 42000,
            "lock": {"fee_cents": 1250},
            "trend": {"points_cents": [], "change_percent": 0},
        },
    )
    db.add(snapshot)
    db.commit()
    return snapshot


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
    fare = _add_fare_snapshot(db, departure)

    response = client.post(
        "/locks/farelock/purchase",
        headers=_auth_header(user),
        json={
            "fare_id": fare.id,
            "expected_fee_cents": 1250,
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
    fare = _add_fare_snapshot(db, departure)

    response = client.post(
        "/locks/farelock/purchase",
        headers=_auth_header(user),
        json={
            "fare_id": fare.id,
            "expected_fee_cents": 1250,
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_funds"
    assert detail["balance_cents"] == 100
    assert detail["fee_cents"] == 1250
    assert detail["shortfall_cents"] == 1150

    db.refresh(user)
    assert user.balance_cents == 100
    assert db.scalars(select(Lock)).all() == []
    assert db.scalars(select(LedgerEntry)).all() == []


def test_purchase_rejects_changed_fee_without_mutating(client, db, mock_price):
    user = _create_user(db, balance_cents=10_000)
    departure = date.today() + timedelta(days=10)
    fare = _add_fare_snapshot(db, departure)

    response = client.post(
        "/locks/farelock/purchase",
        headers=_auth_header(user),
        json={"fare_id": fare.id, "expected_fee_cents": 1200},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "fee_changed",
        "message": "The FareLock fee changed. Review the new price before buying.",
        "expected_fee_cents": 1200,
        "fee_cents": 1250,
    }
    db.refresh(user)
    assert user.balance_cents == 10_000
    assert db.scalars(select(Lock)).all() == []
    assert db.scalars(select(LedgerEntry)).all() == []


def test_purchase_rejects_a_second_open_lock_without_a_second_debit(
    client, db, mock_price
):
    user = _create_user(db, balance_cents=10_000)
    departure = date.today() + timedelta(days=10)
    fare = _add_fare_snapshot(db, departure)
    request = {
        "headers": _auth_header(user),
        "json": {"fare_id": fare.id, "expected_fee_cents": 1250},
    }

    first = client.post("/locks/farelock/purchase", **request)
    second = client.post("/locks/farelock/purchase", **request)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "lock_already_open"
    db.refresh(user)
    assert user.balance_cents == 8750
    assert len(db.scalars(select(Lock)).all()) == 1
    assert len(db.scalars(select(LedgerEntry)).all()) == 1


def test_purchase_requires_auth(client, db):
    departure = date.today() + timedelta(days=10)
    fare = _add_fare_snapshot(db, departure)
    response = client.post(
        "/locks/farelock/purchase",
        json={
            "fare_id": fare.id,
            "expected_fee_cents": 1250,
        },
    )
    assert response.status_code == 401


def test_photon_purchase_debits_the_account_linked_to_the_sender(
    client, db, mock_price, monkeypatch
):
    monkeypatch.setattr(settings, "photon_messaging_secret", "photon-test-secret")
    user = _create_user(db, balance_cents=10_000)
    user.phone_number = "+15551234567"
    db.commit()
    departure = date.today() + timedelta(days=10)
    monkeypatch.setattr(
        locks_router,
        "parse_natural_farelock_quote",
        lambda _query: SimpleNamespace(
            origin="ATL", destination="LAX", departure_date=departure,
            flight_number="DL 123", fare_paid=420.0,
        ),
    )

    response = client.post(
        "/locks/farelock/photon-purchase",
        headers={"X-Photon-Internal-Secret": "photon-test-secret"},
        json={"phone_number": "+15551234567", "query": "lock my flight"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["balance_cents"] == 8750
    assert body["flight_number"] == "DL123"
    lock = db.get(Lock, body["id"])
    assert lock is not None
    assert lock.user_id == user.id
    assert lock.flight_no == "DL123"
    listed = client.get("/locks", headers=_auth_header(user))
    assert listed.status_code == 200
    assert listed.json()["locks"][0]["id"] == body["id"]
    assert listed.json()["locks"][0]["flight_number"] == "DL123"
    assert listed.json()["locks"][0]["fare_id"] is None


def test_photon_purchase_requires_a_flight_number(
    client, db, mock_price, monkeypatch
):
    monkeypatch.setattr(settings, "photon_messaging_secret", "photon-test-secret")
    user = _create_user(db, balance_cents=10_000)
    user.phone_number = "+15551234567"
    db.commit()
    departure = date.today() + timedelta(days=10)
    monkeypatch.setattr(
        locks_router,
        "parse_natural_farelock_quote",
        lambda _query: SimpleNamespace(
            origin="ATL", destination="LAX", departure_date=departure,
            flight_number=None, fare_paid=420.0,
        ),
    )

    response = client.post(
        "/locks/farelock/photon-purchase",
        headers={"X-Photon-Internal-Secret": "photon-test-secret"},
        json={"phone_number": "+15551234567", "query": "lock my flight"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "missing_flight_number"
    assert db.scalar(select(Lock).where(Lock.user_id == user.id)) is None


def test_photon_purchase_reports_shortfall_without_creating_a_lock(
    client, db, mock_price, monkeypatch
):
    monkeypatch.setattr(settings, "photon_messaging_secret", "photon-test-secret")
    user = _create_user(db, balance_cents=100)
    user.phone_number = "+15551234567"
    db.commit()
    departure = date.today() + timedelta(days=10)
    monkeypatch.setattr(
        locks_router,
        "parse_natural_farelock_quote",
        lambda _query: SimpleNamespace(
            origin="ATL", destination="LAX", departure_date=departure,
            flight_number="DL 123", fare_paid=420.0,
        ),
    )

    response = client.post(
        "/locks/farelock/photon-purchase",
        headers={"X-Photon-Internal-Secret": "photon-test-secret"},
        json={"phone_number": "+15551234567", "query": "lock my flight"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["shortfall_cents"] == 1150
    assert db.scalars(select(Lock)).all() == []
