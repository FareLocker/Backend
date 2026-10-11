"""Unit tests for the FastFlight provider integration.

The ``fast_flights`` client performs a live Google Flights request. These
tests replace that client at the service boundary, making them deterministic
and network independent.
"""

from datetime import date
from unittest.mock import Mock

import pytest

from app.services import fastflight


@pytest.fixture
def provider(monkeypatch):  # noqa: ANN001
    """Replace the external FastFlight calls and return their mocks."""
    query = object()
    create_query = Mock(return_value=query)
    get_flights = Mock(return_value=[{"id": "flight-1"}])
    monkeypatch.setattr(fastflight, "create_query", create_query)
    monkeypatch.setattr(fastflight, "get_flights", get_flights)
    monkeypatch.setattr(fastflight.settings, "sim_flights_enabled", False)
    return query, create_query, get_flights


class TestSearchFlights:
    def test_builds_a_default_one_way_query(self, provider) -> None:  # noqa: ANN001
        query, create_query, get_flights = provider

        result = fastflight.search_flights(
            origin="atl ", destination=" lax", departure_date=date(2026, 10, 15)
        )

        print(f"FastFlight result: {result}")
        assert result == [{"id": "flight-1"}]
        create_query.assert_called_once()
        arguments = create_query.call_args.kwargs
        assert arguments["trip"] == "one-way"
        assert arguments["seat"] == "economy"
        assert arguments["language"] == "en-US"
        assert arguments["currency"] == "USD"
        assert arguments["flights"] == [
            fastflight.FlightQuery(
                date="2026-10-15", from_airport="ATL", to_airport="LAX"
            )
        ]
        assert arguments["passengers"] == fastflight.Passengers(adults=1)
        get_flights.assert_called_once_with(query)

    def test_passes_passenger_and_search_preferences_to_provider(self, provider) -> None:  # noqa: ANN001
        _, create_query, _ = provider

        _ = fastflight.search_flights(
            origin="JFK",
            destination="LHR",
            departure_date=date(2026, 11, 1),
            adults=2,
            children=1,
            infants_in_seat=1,
            seat="business",
            currency="GBP",
            language="en-GB",
        )

        arguments = create_query.call_args.kwargs
        assert arguments["seat"] == "business"
        assert arguments["currency"] == "GBP"
        assert arguments["language"] == "en-GB"
        assert arguments["passengers"] == fastflight.Passengers(
            adults=2, children=1, infants_in_seat=1, infants_on_lap=0
        )

    @pytest.mark.parametrize("origin,destination", [("AB", "LAX"), ("ATL", "XYZA")])
    def test_rejects_non_iata_airport_codes(self, provider, origin: str, destination: str) -> None:  # noqa: ANN001
        with pytest.raises(ValueError, match="three-letter airport codes"):
            _ = fastflight.search_flights(
                origin=origin, destination=destination, departure_date=date(2026, 10, 15)
            )

    @pytest.mark.parametrize("adults", [0, -1])
    def test_requires_at_least_one_adult(self, provider, adults: int) -> None:  # noqa: ANN001
        with pytest.raises(ValueError, match="At least one adult"):
            _ = fastflight.search_flights(
                origin="ATL", destination="LAX", departure_date=date(2026, 10, 15), adults=adults
            )

    @pytest.mark.parametrize("flight_number", ["", "AA!123"])
    def test_rejects_invalid_flight_numbers(self, provider, flight_number: str) -> None:  # noqa: ANN001
        with pytest.raises(ValueError, match="Flight number must be alphanumeric"):
            _ = fastflight.search_flights(
                origin="ATL",
                destination="LAX",
                departure_date=date(2026, 10, 15),
                flight_number=flight_number,
            )

    def test_accepts_a_trimmed_alphanumeric_flight_number(self, provider) -> None:  # noqa: ANN001
        _, create_query, _ = provider

        _ = fastflight.search_flights(
            origin="ATL",
            destination="LAX",
            departure_date=date(2026, 10, 15),
            flight_number=" AA123 ",
        )

        create_query.assert_called_once()
