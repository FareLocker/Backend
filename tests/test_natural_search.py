import asyncio
from datetime import date
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.routers import search
from app.schemas import FlightSearchRequest, NaturalFlightSearchRequest
from app.services.natural_search import SearchParseError, parse_natural_search

REFERENCE_DATE = date(2026, 10, 9)  # Friday


@pytest.mark.parametrize(
    "query",
    [
        "ATL to LAX",
        "flights from atl to lax",
        "ATL -> LAX",
        "show me ATL → LAX tomorrow",
    ],
)
def test_parses_supported_route_forms(query: str) -> None:
    request = parse_natural_search(query, reference_date=REFERENCE_DATE)
    assert request.origin == "ATL"
    assert request.destination == "LAX"


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("2026-11-15", date(2026, 11, 15)),
        ("November 15", date(2026, 11, 15)),
        ("Nov 15, 2027", date(2027, 11, 15)),
        ("today", date(2026, 10, 9)),
        ("tomorrow", date(2026, 10, 10)),
        ("next Friday", date(2026, 10, 16)),
    ],
)
def test_parses_supported_dates(phrase: str, expected: date) -> None:
    request = parse_natural_search(
        f"ATL to LAX {phrase}", reference_date=REFERENCE_DATE
    )
    assert request.departure_date == expected


def test_defaults_departure_to_tomorrow() -> None:
    request = parse_natural_search(
        "flights from ATL to LAX", reference_date=REFERENCE_DATE
    )
    assert request.departure_date == date(2026, 10, 10)
    assert request.adults == 1
    assert request.seat == "economy"
    assert request.currency == "USD"
    assert request.language == "en-US"


def test_month_day_rolls_into_next_year() -> None:
    request = parse_natural_search(
        "ATL to LAX January 5", reference_date=REFERENCE_DATE
    )
    assert request.departure_date == date(2027, 1, 5)


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("find me a flight tomorrow", "missing_route"),
        ("ATL to LAX and JFK to MIA", "ambiguous_route"),
        ("ATL to LAX February 30", "invalid_date"),
        ("ATL to LAX tomorrow next Friday", "ambiguous_date"),
        ("ATL to LAX 2026-10-08", "past_date"),
    ],
)
def test_rejects_unresolvable_queries(query: str, code: str) -> None:
    with pytest.raises(SearchParseError) as caught:
        parse_natural_search(query, reference_date=REFERENCE_DATE)
    assert caught.value.code == code


def test_natural_and_structured_search_use_identical_provider_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = Mock(return_value=[{"id": "flight-1"}])
    monkeypatch.setattr(search, "search_flights", provider)
    monkeypatch.setattr(
        search,
        "parse_natural_search",
        lambda _query: FlightSearchRequest(
            origin="ATL",
            destination="LAX",
            departure_date=date(2026, 10, 10),
        ),
    )

    structured = FlightSearchRequest(
        origin="ATL",
        destination="LAX",
        departure_date=date(2026, 10, 10),
    )
    structured_response = asyncio.run(search.search_quote(structured))
    structured_call = provider.call_args

    provider.reset_mock()
    natural_response = asyncio.run(
        search.search_natural(
            NaturalFlightSearchRequest(query="flights from ATL to LAX")
        )
    )

    assert provider.call_args == structured_call
    assert natural_response["results"] == structured_response["results"]


def test_natural_endpoint_returns_structured_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        search,
        "parse_natural_search",
        lambda _query: (_ for _ in ()).throw(
            SearchParseError("missing_route", "Include a route.")
        ),
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            search.search_natural(NaturalFlightSearchRequest(query="tomorrow"))
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "code": "missing_route",
        "message": "Include a route.",
    }
