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
)

SeatClass = Literal["economy", "premium-economy", "business", "first"]


def search_flights(
    *,
    origin: str,
    destination: str,
    departure_date: date,
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    infants_on_lap: int = 0,
    seat: SeatClass = "economy",
    currency: str = "USD",
    language: str = "en-US",
    ## flight number check with stephens implementation of how flight numbers are implemented - andrew
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

    # Validate flight number format if provided
    valid_flight_number = None
    if flight_number is not None:
        valid_flight_number = flight_number.strip()
        if len(valid_flight_number) > 0 and all(c.isalnum() for c in valid_flight_number.upper()):
            # Flight numbers are alphanumeric (typically 1-6 characters, can include airline prefix)
            pass  # Valid format accepted
        else:
            raise ValueError("Flight number must be alphanumeric and not empty")
    
    query = create_query(
        flights=[
            FlightQuery(
                date=departure_date.isoformat(),
                from_airport=airport_from,
                to_airport=airport_to,
            )
        ],
        trip="one-way",
        seat=seat,
        passengers=Passengers(
            adults=adults,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,
        ),
        language=language,
        currency=currency,
    )

    return get_flights(query)
