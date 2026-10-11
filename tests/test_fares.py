import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, FareObservation, FareSnapshot
from app.routers import fares
from app.routers.fares import _history, _normalize_itinerary, _trend, fare_id
from app.schemas import FlightSearchRequest, NaturalFlightSearchRequest


def session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_history_keeps_the_latest_price_for_each_observed_day():
    db = session()
    departure = date(2026, 11, 15)
    db.add_all(
        [
            FareObservation(
                queried_at=datetime(2026, 10, 1, 10, tzinfo=UTC), origin="ATL", dest="LAX",
                departure_date=departure, d=45, dow=6, airline="Delta", flight_no="DL 123",
                cabin="economy", price=100,
            ),
            FareObservation(
                queried_at=datetime(2026, 10, 1, 18, tzinfo=UTC), origin="ATL", dest="LAX",
                departure_date=departure, d=45, dow=6, airline="Delta", flight_no="DL123",
                cabin="economy", price=110,
            ),
            FareObservation(
                queried_at=datetime(2026, 10, 2, 10, tzinfo=UTC), origin="ATL", dest="LAX",
                departure_date=departure, d=44, dow=6, airline="Delta", flight_no="DL123",
                cabin="economy", price=120,
            ),
        ]
    )
    db.commit()

    history = _history(db, origin="ATL", destination="LAX", departure=departure, flight_no="DL123")

    assert [(point.date, point.amount_cents) for point in history] == [
        (date(2026, 10, 1), 11000),
        (date(2026, 10, 2), 12000),
    ]
    assert _trend(history).change_percent == 9.1


def test_normalized_live_itinerary_uses_a_stable_id_and_empty_history():
    db = session()
    itinerary = SimpleNamespace(
        price=425,
        airlines=["Delta"],
        flights=[
            SimpleNamespace(
                from_airport=SimpleNamespace(code="ATL", name="Hartsfield-Jackson Atlanta International"),
                to_airport=SimpleNamespace(code="LAX", name="Los Angeles International"),
                departure=SimpleNamespace(date=(2026, 11, 15), time=(8, 30)),
                arrival=SimpleNamespace(date=(2026, 11, 15), time=(10, 45)),
                duration=315,
                flight_number="DL 123",
            )
        ],
    )

    fare = _normalize_itinerary(itinerary, date(2026, 11, 15), db)

    assert fare is not None
    assert fare.id == fare_id("ATL", "LAX", date(2026, 11, 15), "DL123")
    assert fare.fare_cents == 42500
    assert fare.depart.local_time == "2026-11-15T08:30"
    assert fare.trend.points_cents == []


def test_fare_detail_uses_google_price_history_when_available(
    monkeypatch: pytest.MonkeyPatch,
):
    db = session()
    departure = date(2026, 11, 15)
    fare = fares.FareSearchResultResponse(
        id=fare_id("ATL", "MIA", departure, "F93442"),
        airline_name="Frontier",
        flight_number="F93442",
        cabin_class="Economy",
        stop_airports=[],
        depart=fares.FareEndpointResponse(
            airport_code="ATL", airport_name="Atlanta", city="ATL", local_time="2026-11-15T08:00"
        ),
        arrive=fares.FareEndpointResponse(
            airport_code="MIA", airport_name="Miami", city="MIA", local_time="2026-11-15T10:00"
        ),
        duration_minutes=120,
        fare_cents=12000,
        lock=None,
        trend=fares.FareTrendResponse(points_cents=[], change_percent=0),
    )
    db.add(FareSnapshot(id=fare.id, payload=fare.model_dump()))
    db.commit()
    monkeypatch.setattr(
        fares,
        "fetch_price_history",
        lambda **_kwargs: SimpleNamespace(
            price_history=[
                SimpleNamespace(iso_date="2026-10-01", price=95),
                SimpleNamespace(iso_date="2026-10-02", price=100),
            ]
        ),
    )

    response = fares.get_fare_detail(fare.id, db)

    assert response.history_source == "google"
    assert [(point.date, point.amount_cents) for point in response.history] == [
        (date(2026, 10, 1), 9500),
        (date(2026, 10, 2), 10000),
    ]


def test_fare_detail_refreshes_live_sim_fare_cents(monkeypatch: pytest.MonkeyPatch):
    db = session()
    departure = date.today() + __import__("datetime").timedelta(days=10)
    fare = fares.FareSearchResultResponse(
        id=fare_id("ATL", "LAX", departure, "XD101"),
        airline_name="FareLocker Demo Air",
        flight_number="XD101",
        cabin_class="Economy",
        stop_airports=[],
        depart=fares.FareEndpointResponse(
            airport_code="ATL",
            airport_name="Atlanta",
            city="ATL",
            local_time=f"{departure.isoformat()}T09:15",
        ),
        arrive=fares.FareEndpointResponse(
            airport_code="LAX",
            airport_name="Los Angeles",
            city="LAX",
            local_time=f"{departure.isoformat()}T13:50",
        ),
        duration_minutes=275,
        fare_cents=1,  # stale snapshot
        lock=None,
        trend=fares.FareTrendResponse(points_cents=[], change_percent=0),
        source="sim",
    )
    db.add(FareSnapshot(id=fare.id, payload=fare.model_dump()))
    db.commit()
    monkeypatch.setattr(fares.sim_flights, "price_at", lambda _fn, **_kw: 211.0)
    monkeypatch.setattr(
        fares,
        "quote_lock",
        lambda **_kwargs: __import__("types").SimpleNamespace(
            fee=18.5, K=211.0, T_days=10
        ),
    )

    response = fares.get_fare_detail(fare.id, db)

    assert response.history_source == "sim"
    assert response.fare.fare_cents == 21100
    assert response.fare.source == "sim"
    assert response.fare.lock is not None
    assert response.fare.lock.fee_cents == 1850


def test_fare_detail_falls_back_to_local_history_when_google_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    db = session()
    departure = date(2026, 11, 15)
    fare = fares.FareSearchResultResponse(
        id=fare_id("ATL", "MIA", departure, "F93442"),
        airline_name="Frontier",
        flight_number="F93442",
        cabin_class="Economy",
        stop_airports=[],
        depart=fares.FareEndpointResponse(
            airport_code="ATL", airport_name="Atlanta", city="ATL", local_time="2026-11-15T08:00"
        ),
        arrive=fares.FareEndpointResponse(
            airport_code="MIA", airport_name="Miami", city="MIA", local_time="2026-11-15T10:00"
        ),
        duration_minutes=120,
        fare_cents=12000,
        lock=None,
        trend=fares.FareTrendResponse(points_cents=[], change_percent=0),
    )
    db.add_all(
        [
            FareSnapshot(id=fare.id, payload=fare.model_dump()),
            FareObservation(
                queried_at=datetime(2026, 10, 1, 10, tzinfo=UTC), origin="ATL", dest="MIA",
                departure_date=departure, d=45, dow=6, airline="Frontier", flight_no="F93442",
                cabin="economy", price=95,
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(
        fares,
        "fetch_price_history",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Google unavailable")),
    )

    response = fares.get_fare_detail(fare.id, db)

    assert response.history_source == "local"
    assert [(point.date, point.amount_cents) for point in response.history] == [
        (date(2026, 10, 1), 9500),
    ]


def test_search_fares_deduplicates_provider_offers_before_storing_snapshots(
    monkeypatch: pytest.MonkeyPatch,
):
    db = session()
    departure = date(2026, 11, 15)
    first_flight = SimpleNamespace(
        from_airport=SimpleNamespace(code="ATL", name="Atlanta"),
        to_airport=SimpleNamespace(code="MIA", name="Miami"),
        departure=SimpleNamespace(date=(2026, 11, 15), time=(8, 30)),
        arrival=SimpleNamespace(date=(2026, 11, 15), time=(10, 30)),
        duration=120,
        flight_number="WN4625",
    )
    duplicate_offers = [
        SimpleNamespace(price=90, airlines=["Southwest"], flights=[first_flight]),
        SimpleNamespace(price=95, airlines=["Southwest"], flights=[first_flight]),
    ]
    monkeypatch.setattr(
        fares,
        "parse_natural_search",
        lambda _query: FlightSearchRequest(
            origin="ATL", destination="MIA", departure_date=departure
        ),
    )
    monkeypatch.setattr(fares, "search_flights", lambda **_kwargs: duplicate_offers)

    response = asyncio.run(fares.search_fares(NaturalFlightSearchRequest(query="ATL to MIA"), db))

    assert [result.fare_cents for result in response.results] == [9000]
    assert len(db.query(FareSnapshot).all()) == 1


@pytest.mark.parametrize(
    ("provider_error", "status_code", "code"),
    [
        (ValueError("Origin and destination must be three-letter airport codes"), 422, "invalid_search"),
        (RuntimeError("provider unavailable"), 503, "search_unavailable"),
    ],
)
def test_search_fares_returns_controlled_errors_for_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: Exception,
    status_code: int,
    code: str,
):
    db = session()
    monkeypatch.setattr(
        fares,
        "parse_natural_search",
        lambda _query: FlightSearchRequest(
            origin="ATL",
            destination="LAX",
            departure_date=date(2026, 11, 15),
        ),
    )
    monkeypatch.setattr(
        fares,
        "search_flights",
        lambda **_kwargs: (_ for _ in ()).throw(provider_error),
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(fares.search_fares(NaturalFlightSearchRequest(query="ATL to LAX"), db))

    assert caught.value.status_code == status_code
    assert caught.value.detail["code"] == code  # pyright: ignore[reportArgumentType]
