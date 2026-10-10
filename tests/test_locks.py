"""Flight-day settlement (purchase is covered in test_lock_purchase.py)."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import jobs
from app.auth import hash_password
from app.models import Base, FareObservation, LedgerEntry, Lock, User
from app.services import locks

DEPARTURE = date(2026, 10, 20)
BOUGHT = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
FLIGHT_DAY = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _user(db, *, email: str = "traveler@example.com", balance_cents: int = 0) -> User:
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


def _open_lock(
    db,
    user: User,
    *,
    flight_no: str | None = "DL123",
    strike: float = 100.0,
    fee: float = 10.0,
) -> Lock:
    lock = Lock(
        user_id=user.id,
        route="ATL-JFK",
        flight_no=flight_no,
        departure_date=DEPARTURE,
        K=strike,
        T_days=10,
        d0=10,
        model_value=8.0,
        fee=fee,
        lam=0.2,
        sigma_used=0.3,
        status="open",
        bought_at=BOUGHT,
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)
    return lock


def itinerary(
    price,
    *,
    number="DL123",
    origin="ATL",
    destination="JFK",
    day=DEPARTURE,
    legs=1,
):
    flight = SimpleNamespace(
        from_airport=SimpleNamespace(code=origin),
        to_airport=SimpleNamespace(code=destination),
        departure=SimpleNamespace(date=(day.year, day.month, day.day)),
        flight_number=number,
        codeshares=[],
    )
    return SimpleNamespace(price=price, flights=[flight] * legs)


def test_provider_matching_price_drop_payout_and_repeat_run(sessions, monkeypatch):
    with sessions() as db:
        user = _user(db, balance_cents=0)
        lock_id = _open_lock(db, user).id
    monkeypatch.setattr(
        locks,
        "search_flights",
        lambda **_: [
            itinerary(999, number="AA1"),
            itinerary(300, number="DL123", legs=2),
            itinerary(None),
            itinerary(160, number="DL123", day=DEPARTURE + timedelta(days=1)),
            itinerary(79.745),
            itinerary(125),
        ],
    )
    with sessions() as db:
        assert locks.settle_lock(db, lock_id, now=FLIGHT_DAY - timedelta(days=1)) is None
        lock = locks.settle_lock(db, lock_id, now=FLIGHT_DAY)
        assert lock.status == "exercised"
        assert lock.settle_fare == 79.745
        assert lock.payout == 20.26
        assert locks.settle_lock(db, lock_id, now=FLIGHT_DAY) is None
        assert db.get(Lock, lock_id).payout == 20.26
        user = db.get(User, lock.user_id)
        assert user.balance_cents == 2026
        entry = db.query(LedgerEntry).filter_by(lock_id=lock_id, entry_type="payout").one()
        assert entry.amount_cents == 2026


def test_fallback_uses_later_matching_observation(sessions, monkeypatch):
    with sessions() as db:
        user = _user(db)
        lock_id = _open_lock(db, user).id
        for when, price, number in [
            (BOUGHT - timedelta(hours=1), 200, "DL123"),
            (BOUGHT + timedelta(hours=1), 180, "AA1"),
            (BOUGHT + timedelta(hours=2), 90, "DL123"),
        ]:
            db.add(
                FareObservation(
                    queried_at=when,
                    origin="ATL",
                    dest="JFK",
                    departure_date=DEPARTURE,
                    d=10,
                    dow=DEPARTURE.weekday(),
                    airline="Delta",
                    flight_no=number,
                    cabin="economy",
                    price=price,
                )
            )
        db.commit()
    monkeypatch.setattr(locks, "search_flights", lambda **_: [])
    with sessions() as db:
        lock = locks.settle_lock(db, lock_id, now=FLIGHT_DAY)
        assert lock.status == "exercised"
        assert lock.settle_fare == 90
        assert lock.payout == 10


def test_provider_error_pending_then_worker_retries(sessions, monkeypatch):
    with sessions() as db:
        user = _user(db)
        lock_id = _open_lock(db, user).id

    def outage(**_):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(locks, "search_flights", outage)
    assert jobs.settle_due_locks(now=FLIGHT_DAY) == {
        "settled": 0,
        "pending_verification": 1,
        "failed": 0,
    }
    with sessions() as db:
        lock = db.get(Lock, lock_id)
        assert lock.status == "pending_verification"
        assert lock.settled_at is None
        assert lock.payout is None

    monkeypatch.setattr(locks, "search_flights", lambda **_: [itinerary(65)])
    assert jobs.settle_due_locks(now=FLIGHT_DAY) == {
        "settled": 1,
        "pending_verification": 0,
        "failed": 0,
    }
    assert jobs.settle_due_locks(now=FLIGHT_DAY) == {
        "settled": 0,
        "pending_verification": 0,
        "failed": 0,
    }
    with sessions() as db:
        assert db.get(Lock, lock_id).payout == 35


def test_route_lock_uses_cheapest_valid_one_way_fare(sessions, monkeypatch):
    with sessions() as db:
        user = _user(db)
        lock_id = _open_lock(db, user, flight_no=None).id
    monkeypatch.setattr(
        locks,
        "search_flights",
        lambda **_: [
            itinerary(80, destination="LAX"),
            itinerary(None),
            itinerary(130),
            itinerary(110, number="AA1"),
        ],
    )
    with sessions() as db:
        lock = locks.settle_lock(db, lock_id, now=FLIGHT_DAY)
        assert lock.settle_fare == 110
        assert lock.payout == 0


def test_settlement_payout_is_capped_at_half_the_paid_fare(sessions, monkeypatch):
    with sessions() as db:
        user = _user(db)
        lock_id = _open_lock(db, user).id
    monkeypatch.setattr(locks, "search_flights", lambda **_: [itinerary(1)])
    with sessions() as db:
        lock = locks.settle_lock(db, lock_id, now=FLIGHT_DAY)
        assert lock.status == "exercised"
        assert lock.payout == 50
