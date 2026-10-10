from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    return_date: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    infants_in_seat: int = Field(default=0, ge=0, le=8)
    infants_on_lap: int = Field(default=0, ge=0, le=8)
    seat: Literal["economy", "premium-economy", "business", "first"] = "economy"
    currency: str = "USD"
    language: str = "en-US"
    max_stops: int | None = Field(default=None, ge=0)
    airlines: list[str] | None = None
    earliest_departure_hour: int | None = Field(default=None, ge=0, le=23)
    latest_departure_hour: int | None = Field(default=None, ge=0, le=23)
    earliest_arrival_hour: int | None = Field(default=None, ge=0, le=23)
    latest_arrival_hour: int | None = Field(default=None, ge=0, le=23)
    max_duration_minutes: int | None = Field(default=None, gt=0)
    connecting_airports: list[str] | None = None
    min_layover_minutes: int | None = Field(default=None, ge=0)
    max_layover_minutes: int | None = Field(default=None, ge=0)
    less_emissions_only: bool = False
    max_price: int | None = Field(default=None, gt=0)
    carry_on_bags: int = Field(default=0, ge=0)
    checked_bags: int = Field(default=0, ge=0)
    hide_separate_and_self_transfer: bool = False
    exclude_basic_economy: bool = False
    flight_number: str | None = None
    context: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_search_constraints(self) -> "FlightSearchRequest":
        passenger_count = (
            self.adults
            + self.children
            + self.infants_in_seat
            + self.infants_on_lap
        )
        if passenger_count > 9:
            raise ValueError("A search can include at most 9 passengers")
        if self.infants_on_lap > self.adults:
            raise ValueError("Each infant on lap requires an adult")
        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("Return date cannot be before departure date")
        if (
            self.min_layover_minutes is not None
            and self.max_layover_minutes is not None
            and self.min_layover_minutes > self.max_layover_minutes
        ):
            raise ValueError("Minimum layover cannot exceed maximum layover")
        return self
