"""Google Flights search integration.

The provider call is intentionally isolated here so routers can remain focused on
HTTP validation and tests can replace the upstream request without using the
network.
"""

from datetime import date
from typing import Literal

# fast-flights api
from fast_flights import (
    FlightQuery,
    Passengers,
    ResultList,
    create_query,
    get_flights,
    get_price_history as _get_price_history,
)

SeatClass = Literal["economy", "premium-economy", "business", "first"]


def search_flights(
    *,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date | None = None,
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    infants_on_lap: int = 0,
    seat: SeatClass = "economy",
    currency: str = "USD",
    language: str = "en-US",
    max_stops: int | None = None,
    airlines: list[str] | None = None,
    earliest_departure_hour: int | None = None,
    latest_departure_hour: int | None = None,
    earliest_arrival_hour: int | None = None,
    latest_arrival_hour: int | None = None,
    max_duration_minutes: int | None = None,
    connecting_airports: list[str] | None = None,
    min_layover_minutes: int | None = None,
    max_layover_minutes: int | None = None,
    less_emissions_only: bool = False,
    max_price: int | None = None,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    hide_separate_and_self_transfer: bool = False,
    exclude_basic_economy: bool = False,
    flight_number: str | None = None,
) -> ResultList:
    """Search one-way flights using the direct ``fast_flights`` integration.

    Args:
        origin: Three-letter origin airport code.
        destination: Three-letter destination airport code.
        departure_date: Date to search for flights.
        adults: Number of adult passengers.
        children: Number of child passengers.
        infants_in_seat: Number of infants with a seat.
        infants_on_lap: Number of infants on lap.
        seat: Seat class preference.
        currency: Currency code.
        language: Language preference.
        flight_number: Optional specific flight number to filter results.

    Returns:
        Flight search results matching the query.
    """
    if adults < 1:
        raise ValueError("At least one adult passenger is required")

    airport_from = origin.strip().upper()
    airport_to = destination.strip().upper()
    if len(airport_from) != 3 or len(airport_to) != 3:
        raise ValueError("Origin and destination must be three-letter airport codes")

    valid_flight_number: str | None = None
    if flight_number is not None:
        valid_flight_number = "".join(flight_number.upper().split())
        if not valid_flight_number or not valid_flight_number.isalnum():
            raise ValueError("Flight number must be alphanumeric and not empty")

    normalized_airlines = (
        [airline.strip().upper() for airline in airlines if airline.strip()]
        if airlines
        else None
    )
    normalized_connections = (
        [airport.strip().upper() for airport in connecting_airports if airport.strip()]
        if connecting_airports
        else None
    )

    def make_leg(*, leg_date: date, from_airport: str, to_airport: str) -> FlightQuery:
        return FlightQuery(
            date=leg_date.isoformat(),
            from_airport=from_airport,
            to_airport=to_airport,
            max_stops=max_stops,
            airlines=normalized_airlines,
            earliest_departure_hour=earliest_departure_hour,
            latest_departure_hour=latest_departure_hour,
            earliest_arrival_hour=earliest_arrival_hour,
            latest_arrival_hour=latest_arrival_hour,
            max_duration_minutes=max_duration_minutes,
            connecting_airports=normalized_connections,
            min_layover_minutes=min_layover_minutes,
            max_layover_minutes=max_layover_minutes,
            less_emissions_only=less_emissions_only,
        )

    legs = [
        make_leg(
            leg_date=departure_date,
            from_airport=airport_from,
            to_airport=airport_to,
        )
    ]
    trip: Literal["one-way", "round-trip"] = "one-way"
    if return_date is not None:
        if return_date < departure_date:
            raise ValueError("Return date cannot be before departure date")
        trip = "round-trip"
        legs.append(
            make_leg(
                leg_date=return_date,
                from_airport=airport_to,
                to_airport=airport_from,
            )
        )

    query = create_query(
        flights=legs,
        trip=trip,
        seat=seat,
        passengers=Passengers(
            adults=adults,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,
        ),
        language=language,
        currency=currency,
        max_price=max_price,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        hide_separate_and_self_transfer=hide_separate_and_self_transfer,
        exclude_basic_economy=exclude_basic_economy,
    )

    results = get_flights(query)
    if valid_flight_number is None:
        return results

    def matches_flight_number(itinerary: object) -> bool:
        for leg in getattr(itinerary, "flights", []):
            numbers = [
                getattr(leg, "flight_number", ""),
                *getattr(leg, "codeshares", []),
            ]
            if any(
                "".join(number.upper().split()) == valid_flight_number
                for number in numbers
            ):
                return True
        return False

    filtered = ResultList(
        itinerary for itinerary in results if matches_flight_number(itinerary)
    )
    if hasattr(results, "metadata"):
        filtered.metadata = results.metadata
    return filtered


def fetch_price_history(
    *, flight_number: str, departure_date: date, origin: str, destination: str
):
    return _get_price_history(
        flight_number,
        departure_date.isoformat(),
        origin.upper(),
        destination.upper(),
        currency="USD",
        language="en-US",
    )
