from datetime import date
from typing import Literal

from pydantic import BaseModel


class FlightSearchRequest(BaseModel):
    """Request model for flight search.
    
    Attributes:
        origin: Three-letter origin airport code (e.g., 'JFK').
        destination: Three-letter destination airport code (e.g., 'LAX').
        departure_date: Date to search for flights (YYYY-MM-DD format).
        adults: Number of adult passengers (default: 1).
        children: Number of child passengers (default: 0).
        infants_in_seat: Number of infants with a seat (default: 0).
        infants_on_lap: Number of infants on lap (default: 0).
        seat: Seat class preference ('economy', 'premium-economy', 'business', 'first').
        currency: Currency code (default: 'USD').
        language: Language preference (default: 'en-US').
        flight_number: Optional specific flight number to filter results.
        context: Additional contextual information for the search request.
    """
    origin: str
    destination: str
    departure_date: date
    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0
    seat: Literal["economy", "premium-economy", "business", "first"] = "economy"
    currency: str = "USD"
    language: str = "en-US"
    flight_number: str | None = None
    context: dict[str, object] | None = None
