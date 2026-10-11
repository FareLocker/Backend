"""Simulated XD* demo flights (coexist with Google)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest

from app.constants import ROUTES
from app.models import Lock
from app.services import fastflight, sim_flights
from app.services.locks import _provider_fare, _resolve_current_fare


def test_is_sim_recognizes_catalog_numbers() -> None:
    assert sim_flights.is_sim("XD 101")
    assert sim_flights.is_sim("xd101")
    assert not sim_flights.is_sim("DL501")
    assert not sim_flights.is_sim(None)


def test_price_at_moves_over_time() -> None:
    t0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    # Across ~half a noise knot the path should drift (not a fixed sine).
    t1 = t0 + timedelta(seconds=sim_flights.STEP_SECONDS * 0.5)
    t2 = t0 + timedelta(seconds=sim_flights.STEP_SECONDS * 2.0)
    p0 = sim_flights.price_at("XD101", at=t0)
    p1 = sim_flights.price_at("XD101", at=t1)
    p2 = sim_flights.price_at("XD101", at=t2)
    assert p0 >= sim_flights.PRICE_FLOOR
    assert p1 >= sim_flights.PRICE_FLOOR
    assert p2 >= sim_flights.PRICE_FLOOR
    # Not a pure sine: three spaced samples should not be a symmetric wave.
    assert len({p0, p1, p2}) >= 2


def test_price_paths_differ_by_flight() -> None:
    at = datetime(2026, 10, 10, 15, 0, 0, tzinfo=UTC)
    assert sim_flights.price_at("XD101", at=at) != sim_flights.price_at(
        "XD303", at=at
    )


def test_history_insights_non_empty() -> None:
    insights = sim_flights.history_insights(
        "XD101",
        origin="ATL",
        destination="LAX",
        departure_date=date.today() + timedelta(days=10),
    )
    assert len(insights.price_history) == sim_flights.HISTORY_STEPS
    assert insights.lowest_price is not None
    assert insights.typical_price_range is not None
    assert insights.price_level in {"low", "typical", "high"}


def test_history_is_calendar_days_ending_today() -> None:
    at = datetime(2026, 10, 10, 15, 30, 0, tzinfo=UTC)
    insights = sim_flights.history_insights(
        "XD101",
        origin="ATL",
        destination="LAX",
        departure_date=date(2026, 10, 20),
        at=at,
    )
    dates = [point.iso_date for point in insights.price_history]
    assert dates[0] == "2026-09-11"
    assert dates[-1] == "2026-10-10"
    assert len(dates) == len(set(dates))


def test_history_past_days_stable_across_polls() -> None:
    """Polling minutes apart must not reshuffle completed history bars."""
    t0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=45)
    a = sim_flights.history_insights(
        "XD101",
        origin="ATL",
        destination="LAX",
        departure_date=date(2026, 10, 20),
        at=t0,
    )
    b = sim_flights.history_insights(
        "XD101",
        origin="ATL",
        destination="LAX",
        departure_date=date(2026, 10, 20),
        at=t1,
    )
    past_a = [(p.iso_date, p.price) for p in a.price_history[:-1]]
    past_b = [(p.iso_date, p.price) for p in b.price_history[:-1]]
    assert past_a == past_b
    assert a.price_history[-1].iso_date == b.price_history[-1].iso_date == "2026-10-10"
    # Today's bar may move with the live noise path (stored as whole dollars).
    assert a.price_history[-1].price == int(round(sim_flights.price_at("XD101", at=t0)))
    assert b.price_history[-1].price == int(round(sim_flights.price_at("XD101", at=t1)))


def test_iter_itineraries_matches_route_and_window() -> None:
    today = date(2026, 10, 10)
    departure = today + timedelta(days=7)
    rows = sim_flights.iter_itineraries(
        origin="ATL",
        destination="LAX",
        departure_date=departure,
        today=today,
    )
    numbers = {
        "".join(getattr(leg, "flight_number", "").upper().split())
        for row in rows
        for leg in row.flights
    }
    assert "XD101" in numbers
    assert "XD303" in numbers
    assert all(row.is_sim for row in rows)


def test_catalog_covers_every_supported_route() -> None:
    covered = {(spec.origin, spec.destination) for spec in sim_flights.CATALOG}
    assert covered == set(ROUTES)
    # At least one flight per route; ATL-LAX keeps the two legacy demos.
    for origin, destination in ROUTES:
        rows = sim_flights.iter_itineraries(
            origin=origin,
            destination=destination,
            departure_date=date(2026, 10, 17),
            today=date(2026, 10, 10),
        )
        assert len(rows) >= 1, f"missing sim inventory for {origin}-{destination}"
    assert len(sim_flights.CATALOG) == len(ROUTES) * 2


def test_iter_itineraries_empty_outside_quote_window() -> None:
    today = date(2026, 10, 10)
    rows = sim_flights.iter_itineraries(
        origin="ATL",
        destination="LAX",
        departure_date=today,
        today=today,
    )
    assert rows == []


def test_search_merges_sim_for_atl_lax(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fastflight.settings, "sim_flights_enabled", True)
    monkeypatch.setattr(fastflight, "create_query", Mock(return_value=object()))
    get_flights = Mock(return_value=[{"id": "google-1"}])
    monkeypatch.setattr(fastflight, "get_flights", get_flights)

    departure = date.today() + timedelta(days=10)
    results = fastflight.search_flights(
        origin="ATL", destination="LAX", departure_date=departure
    )

    get_flights.assert_called_once()
    assert results[0] == {"id": "google-1"}
    sim_rows = [row for row in results[1:] if getattr(row, "is_sim", False)]
    assert len(sim_rows) >= 2


def test_search_sim_only_skips_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fastflight.settings, "sim_flights_enabled", True)
    get_flights = Mock(return_value=[{"id": "should-not-run"}])
    monkeypatch.setattr(fastflight, "get_flights", get_flights)
    monkeypatch.setattr(fastflight, "create_query", Mock())

    departure = date.today() + timedelta(days=10)
    results = fastflight.search_flights(
        origin="ATL",
        destination="LAX",
        departure_date=departure,
        flight_number="XD 101",
    )

    get_flights.assert_not_called()
    assert len(results) == 1
    assert results[0].is_sim


def test_fetch_price_history_sim_never_calls_google(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fastflight.settings, "sim_flights_enabled", True)
    google = Mock(side_effect=AssertionError("should not call Google"))
    monkeypatch.setattr(fastflight, "_get_price_history", google)

    insights = fastflight.fetch_price_history(
        flight_number="XD101",
        departure_date=date.today() + timedelta(days=10),
        origin="ATL",
        destination="LAX",
    )

    google.assert_not_called()
    assert insights.price_history
    assert insights.lowest_price is not None


def test_lock_quote_available_for_sim_without_curve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import fares as fares_router
    from app.services import pricer as pricer_mod

    monkeypatch.setattr(pricer_mod, "load_latest_curve", lambda *_a, **_k: None)
    summary = fares_router._lock_quote(
        db=Mock(),
        origin="ATL",
        destination="LAX",
        departure=date.today() + timedelta(days=10),
        flight_no="XD101",
        price=220.0,
    )
    assert summary is not None
    assert summary.fee_cents >= 100


def test_provider_fare_uses_sim_price() -> None:
    lock = Lock(
        user_id=1,
        route="ATL-LAX",
        flight_no="XD101",
        departure_date=date.today() + timedelta(days=10),
        K=220.0,
        T_days=10,
        d0=10,
        model_value=10.0,
        fee=15.0,
        lam=0.3,
        sigma_used=0.35,
        status="open",
        bought_at=datetime.now(UTC),
    )
    fare = _provider_fare(lock)
    assert fare is not None
    assert fare == sim_flights.price_at("XD101")


def test_resolve_current_fare_tags_sim() -> None:
    # Sim short-circuit does not touch the DB.
    lock = Lock(
        user_id=1,
        route="ATL-LAX",
        flight_no="XD101",
        departure_date=date.today() + timedelta(days=10),
        K=220.0,
        T_days=10,
        d0=10,
        model_value=10.0,
        fee=15.0,
        lam=0.3,
        sigma_used=0.35,
        status="open",
        bought_at=datetime.now(UTC),
    )
    fare, source = _resolve_current_fare(lock, db=None)  # type: ignore[arg-type]
    assert source == "sim"
    assert fare == sim_flights.price_at("XD101")


def test_mark_to_market_reports_sim_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import locks as locks_router

    lock = Lock(
        id=1,
        user_id=1,
        route="ATL-LAX",
        flight_no="XD101",
        departure_date=date.today() + timedelta(days=10),
        K=300.0,
        T_days=10,
        d0=10,
        model_value=10.0,
        fee=15.0,
        lam=0.3,
        sigma_used=0.35,
        status="open",
        bought_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        locks_router,
        "_resolve_current_fare",
        lambda _lock, _db: (200.0, "sim"),
    )
    response = locks_router._mark_to_market(lock, db=None)  # type: ignore[arg-type]
    assert response.fare_source == "sim"
    assert response.current_fare_cents == 20000
    assert response.in_the_money is True
    assert response.intrinsic_payout_cents == 10000
