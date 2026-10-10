from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.routers import quotes
from app.schemas import NaturalFareLockQuoteRequest
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
