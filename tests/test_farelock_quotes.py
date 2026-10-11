from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.routers import quotes
from app.schemas import FareLockPriceRequest, NaturalFareLockQuoteRequest
from app.services.natural_search import SearchParseError, parse_natural_farelock_quote


REFERENCE_DATE = date(2026, 10, 10)


def test_parses_natural_farelock_quote() -> None:
    parsed = parse_natural_farelock_quote(
        "I booked a flight from ATL -> LAX on Oct 20 for $420, how much is FareLock?",
        reference_date=REFERENCE_DATE,
    )

    assert parsed.origin == "ATL"
    assert parsed.destination == "LAX"
    assert parsed.departure_date == date(2026, 10, 20)
    assert parsed.fare_paid == 420.0
    assert parsed.flight_number is None


def test_parses_explicit_flight_number_without_confusing_date_context() -> None:
    parsed = parse_natural_farelock_quote(
        "I booked ATL to LAX on 2026-10-20 for $420 on flight dl123",
        reference_date=REFERENCE_DATE,
    )

    assert parsed.flight_number == "DL 123"


def test_parses_city_names_for_farelock_quote() -> None:
    parsed = parse_natural_farelock_quote(
        "I booked a trip from atlanta to miami next Friday for $200, "
        "how much is FareLock?",
        reference_date=REFERENCE_DATE,
    )

    assert parsed.origin == "ATL"
    assert parsed.destination == "MIA"
    assert parsed.departure_date == date(2026, 10, 16)
    assert parsed.fare_paid == 200.0


def test_parses_mixed_city_and_airport_code_for_farelock_quote() -> None:
    parsed = parse_natural_farelock_quote(
        "I booked from Atlanta to LAX next Friday for $284, how much is FareLock?",
        reference_date=REFERENCE_DATE,
    )

    assert parsed.origin == "ATL"
    assert parsed.destination == "LAX"
    assert parsed.departure_date == date(2026, 10, 16)
    assert parsed.fare_paid == 284.0


def test_city_quote_with_fare_asks_only_for_missing_date() -> None:
    with pytest.raises(SearchParseError) as caught:
        parse_natural_farelock_quote(
            "I booked a trip from atlanta to miami for $200, how much is FareLock?",
            reference_date=REFERENCE_DATE,
        )

    assert caught.value.code == "missing_departure_date"
    assert caught.value.message == (
        'What is the departure date? Reply with a date such as "next Friday".'
    )


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("I booked ATL to LAX for $420", "missing_departure_date"),
        ("I booked ATL to LAX on Oct 20", "missing_fare_paid"),
    ],
)
def test_farelock_quote_requires_date_and_fare(query: str, code: str) -> None:
    with pytest.raises(SearchParseError) as caught:
        parse_natural_farelock_quote(query, reference_date=REFERENCE_DATE)

    assert caught.value.code == code


def test_farelock_endpoint_returns_customer_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    departure = date.today() + timedelta(days=10)
    pricing = MagicMock(
        return_value=SimpleNamespace(K=420.0, fee=37.2, T_days=10)
    )
    monkeypatch.setattr(quotes, "quote_lock", pricing)
    db = MagicMock()

    response = quotes.farelock_quote(
        NaturalFareLockQuoteRequest(
            query=f"I booked ATL to LAX on {departure.isoformat()} for $420"
        ),
        db,
    )

    assert response.origin == "ATL"
    assert response.destination == "LAX"
    assert response.fare_paid == 420.0
    assert response.fee == 37.2
    assert response.max_potential_payout == 210.0
    assert response.protection_days == 10
    pricing.assert_called_once_with(
        route=("ATL", "LAX"),
        flight_no=None,
        _departure_date=departure,
        S0=420.0,
        d0=10,
        dow=departure.weekday(),
        db=db,
    )


def test_farelock_endpoint_enforces_quote_window() -> None:
    departure = date.today() + timedelta(days=31)

    with pytest.raises(HTTPException) as caught:
        quotes.farelock_quote(
            NaturalFareLockQuoteRequest(
                query=f"I booked ATL to LAX on {departure.isoformat()} for $420"
            ),
            MagicMock(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "outside_quote_window"


def test_farelock_endpoint_explains_missing_pricing_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    departure = date.today() + timedelta(days=10)
    monkeypatch.setattr(
        quotes,
        "quote_lock",
        MagicMock(side_effect=ValueError("no curve fit for ATL-LAX")),
    )

    with pytest.raises(HTTPException) as caught:
        quotes.farelock_quote(
            NaturalFareLockQuoteRequest(
                query=f"I booked ATL to LAX on {departure.isoformat()} for $420"
            ),
            MagicMock(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "code": "pricing_unavailable",
        "message": (
            "I can’t quote FareLock for ATL to LAX yet because we don’t have enough "
            "historical fare data to calculate a reliable fee. Try another supported "
            "route or check back after more fares have been collected."
        ),
        "route": "ATL-LAX",
        "reason": "historical_fare_data_unavailable",
        "minimum_observations": 30,
    }


def test_farelock_price_endpoint_returns_cents_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    departure = date.today() + timedelta(days=10)
    pricing = MagicMock(
        return_value=SimpleNamespace(K=420.0, fee=37.2, T_days=10)
    )
    monkeypatch.setattr(quotes, "quote_lock", pricing)
    db = MagicMock()

    response = quotes.farelock_price(
        FareLockPriceRequest(
            origin="atl",
            destination="lax",
            departure_date=departure,
            flight_number="dl 123",
            fare_cents=42000,
        ),
        db,
    )

    assert response.origin == "ATL"
    assert response.destination == "LAX"
    assert response.departure_date == departure
    assert response.flight_number == "DL123"
    assert response.fare_cents == 42000
    assert response.strike_cents == 42000
    assert response.fee_cents == 3720
    assert response.max_payout_cents == 21000
    assert response.protection_days == 10
    assert response.currency == "USD"
    pricing.assert_called_once_with(
        route=("ATL", "LAX"),
        flight_no="DL123",
        _departure_date=departure,
        S0=420.0,
        d0=10,
        dow=departure.weekday(),
        db=db,
    )


def test_farelock_price_endpoint_uses_custom_strike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    departure = date.today() + timedelta(days=10)
    pricing = MagicMock(
        return_value=SimpleNamespace(K=400.0, fee=35.0, T_days=10)
    )
    monkeypatch.setattr(quotes, "quote_lock", pricing)
    db = MagicMock()

    response = quotes.farelock_price(
        FareLockPriceRequest(
            origin="ATL",
            destination="LAX",
            departure_date=departure,
            flight_number="DL123",
            fare_cents=42000,
            strike_cents=40000,
        ),
        db,
    )

    assert response.fare_cents == 42000
    assert response.strike_cents == 40000
    assert response.fee_cents == 3500
    assert response.max_payout_cents == 20000
    pricing.assert_called_once_with(
        route=("ATL", "LAX"),
        flight_no="DL123",
        _departure_date=departure,
        S0=400.0,
        d0=10,
        dow=departure.weekday(),
        db=db,
    )


def test_farelock_price_endpoint_enforces_quote_window() -> None:
    departure = date.today() + timedelta(days=31)

    with pytest.raises(HTTPException) as caught:
        quotes.farelock_price(
            FareLockPriceRequest(
                origin="ATL",
                destination="LAX",
                departure_date=departure,
                fare_cents=42000,
            ),
            MagicMock(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "outside_quote_window"


def test_farelock_price_endpoint_rejects_unsupported_route() -> None:
    departure = date.today() + timedelta(days=10)

    with pytest.raises(HTTPException) as caught:
        quotes.farelock_price(
            FareLockPriceRequest(
                origin="XXX",
                destination="YYY",
                departure_date=departure,
                fare_cents=42000,
            ),
            MagicMock(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "unsupported_route"


def test_farelock_price_endpoint_rejects_departure_too_soon() -> None:
    with pytest.raises(HTTPException) as caught:
        quotes.farelock_price(
            FareLockPriceRequest(
                origin="ATL",
                destination="LAX",
                departure_date=date.today(),
                fare_cents=42000,
            ),
            MagicMock(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "departure_too_soon"


def test_farelock_price_endpoint_explains_missing_pricing_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    departure = date.today() + timedelta(days=10)
    monkeypatch.setattr(
        quotes,
        "quote_lock",
        MagicMock(side_effect=ValueError("no curve fit for ATL-LAX")),
    )

    with pytest.raises(HTTPException) as caught:
        quotes.farelock_price(
            FareLockPriceRequest(
                origin="ATL",
                destination="LAX",
                departure_date=departure,
                fare_cents=42000,
            ),
            MagicMock(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "pricing_unavailable"
    assert caught.value.detail["route"] == "ATL-LAX"
    assert caught.value.detail["reason"] == "historical_fare_data_unavailable"
