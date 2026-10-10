from datetime import date, datetime, timedelta, timezone
from math import log

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models import Base, CurveFit, FareObservation, VolFit
from app.routers import market
from app.services.curves import D_BUCKETS


TODAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture
def market_client(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(market, "_today", lambda: TODAY)

    def test_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    try:
        yield TestClient(app), engine
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def add_fare(session: Session, *, price: float, departure: date, queried: datetime):
    session.add(
        FareObservation(
            queried_at=queried,
            origin="ATL",
            dest="JFK",
            departure_date=departure,
            d=(departure - queried.date()).days,
            dow=departure.weekday(),
            airline="Delta",
            flight_no="DL123",
            cabin="economy",
            price=price,
        )
    )


def test_overview_includes_empty_routes_and_latest_upcoming_fare(market_client):
    client, engine = market_client
    with Session(engine) as session:
        add_fare(session, price=90, departure=TODAY - timedelta(days=1), queried=NOW)
        add_fare(session, price=120, departure=TODAY + timedelta(days=2), queried=NOW)
        add_fare(session, price=130, departure=TODAY + timedelta(days=3), queried=NOW)
        session.commit()

    response = client.get("/market")
    assert response.status_code == 200
    routes = response.json()["routes"]
    assert len(routes) == 10
    assert routes[0]["origin"] == "ATL"
    assert routes[0]["destination"] == "JFK"
    assert routes[0]["latest_fare"]["price"] == 130
    assert routes[0]["latest_fare"]["departure_date"] == str(TODAY + timedelta(days=3))
    assert routes[1]["latest_fare"] is None
    assert routes[1]["curve_fitted_at"] is None
    assert response.json()["currency"] == "USD"


def test_detail_filters_date_orders_observations_and_limits(market_client):
    client, engine = market_client
    target = TODAY + timedelta(days=5)
    with Session(engine) as session:
        add_fare(session, price=100, departure=target, queried=NOW - timedelta(hours=2))
        add_fare(session, price=110, departure=target, queried=NOW - timedelta(hours=1))
        add_fare(session, price=200, departure=target + timedelta(days=1), queried=NOW)
        session.commit()

    route_wide = client.get("/market/ATL/JFK?limit=2")
    assert route_wide.status_code == 200
    assert [row["price"] for row in route_wide.json()["observations"]] == [200, 110]

    filtered = client.get(f"/market/atl/jfk?departure_date={target}")
    assert filtered.status_code == 200
    assert [row["price"] for row in filtered.json()["observations"]] == [110, 100]
    assert filtered.json()["departure_date"] == str(target)


def test_detail_returns_latest_fit_and_supported_projections(market_client):
    client, engine = market_client
    old = NOW - timedelta(days=1)
    with Session(engine) as session:
        params = {"intercept": log(100), "holiday": 0}
        params.update({f"d_{lo}_{hi}": 0 for lo, hi in D_BUCKETS[1:]})
        params.update({f"dow_{dow}": 0 for dow in (0, 1, 3, 4, 5, 6)})
        session.add_all(
            [
                CurveFit(route="ATL-JFK", fitted_at=old, params={"intercept": log(50)}),
                CurveFit(route="ATL-JFK", fitted_at=NOW, params=params),
                VolFit(route="ATL-JFK", fitted_at=old, bucket_lo=1, bucket_hi=3, sigma=0.9, n_obs=1, shrink_w=0),
                VolFit(route="ATL-JFK", fitted_at=NOW, bucket_lo=1, bucket_hi=3, sigma=0.2, n_obs=10, shrink_w=0.5),
                VolFit(route="ATL-JFK", fitted_at=NOW, bucket_lo=4, bucket_hi=7, sigma=0.3, n_obs=8, shrink_w=0.4),
            ]
        )
        session.commit()

    detail = client.get("/market/ATL/JFK").json()
    assert len(detail["curve"]["projections"]) == 60
    assert detail["curve"]["projections"][0]["estimated_fare"] == pytest.approx(100)
    assert detail["curve"]["holiday_assumption"] == 0
    assert [bucket["sigma"] for bucket in detail["volatility"]["buckets"]] == [0.2, 0.3]

    selected = client.get(f"/market/ATL/JFK?departure_date={TODAY + timedelta(days=5)}").json()
    assert len(selected["curve"]["projections"]) == 1
    outside = client.get(f"/market/ATL/JFK?departure_date={TODAY + timedelta(days=61)}").json()
    assert outside["curve"]["projections"] == []


def test_detail_empty_and_invalid_requests(market_client):
    client, _ = market_client
    empty = client.get("/market/ATL/JFK")
    assert empty.status_code == 200
    assert empty.json()["observations"] == []
    assert empty.json()["curve"] is None
    assert empty.json()["volatility"] is None
    assert client.get("/market/ATL/BOS").status_code == 404
    assert client.get("/market/ATL/JFK?limit=201").status_code == 422
    assert client.get("/market/ATL/JFK?departure_date=bad").status_code == 422
