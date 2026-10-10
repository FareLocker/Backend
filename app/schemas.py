from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class WalletResponse(BaseModel):
    user_id: int
    balance_cents: int
    currency: Literal["USD"] = "USD"


class WalletAddFundsRequest(BaseModel):
    # Skeleton only: payment authorization and settlement are not implemented.
    amount_cents: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=200)


class WalletAddFundsResponse(BaseModel):
    user_id: int
    balance_cents: int
    credited_cents: int
    ledger_entry_id: int
    currency: Literal["USD"] = "USD"


class FareHistoryPoint(BaseModel):
    date: date
    amount_cents: int


class FareDetailResponse(BaseModel):
    fare: "FareSearchResultResponse"
    history: list[FareHistoryPoint]
    history_source: Literal["google", "local"]


class FareEndpointResponse(BaseModel):
    airport_code: str
    airport_name: str
    city: str
    local_time: str


class FareLockQuoteSummary(BaseModel):
    fee_cents: int


class FareTrendResponse(BaseModel):
    points_cents: list[int]
    change_percent: float


class FareSearchResultResponse(BaseModel):
    id: str
    airline_name: str
    flight_number: str
    cabin_class: str
    stop_airports: list[str]
    depart: FareEndpointResponse
    arrive: FareEndpointResponse
    duration_minutes: int
    fare_cents: int
    lock: FareLockQuoteSummary | None
    trend: FareTrendResponse


class FareSearchResponse(BaseModel):
    results: list[FareSearchResultResponse]


class NaturalFlightSearchRequest(BaseModel):
    """Free-form flight search submitted by the website search box."""

    query: str = Field(min_length=1, max_length=200)

    model_config = {
        "json_schema_extra": {
            "examples": [{"query": "flights from ATL to LAX on 2026-11-15"}]
        }
    }

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("Search query cannot be blank")
        return query


class NaturalFareLockQuoteRequest(BaseModel):
    """Free-form request for a FareLock price quote."""

    query: str = Field(min_length=1, max_length=300)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("FareLock query cannot be blank")
        return query


class FareLockQuoteResponse(BaseModel):
    origin: str
    destination: str
    departure_date: date
    fare_paid: float
    flight_number: str | None
    fee: float
    max_potential_payout: float
    protection_days: int
    currency: Literal["USD"] = "USD"


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
            self.adults + self.children + self.infants_in_seat + self.infants_on_lap
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


class MarketFare(BaseModel):
    price: float
    departure_date: date
    queried_at: datetime
    airline: str | None
    flight_no: str | None
    cabin: str


class MarketRoute(BaseModel):
    origin: str
    destination: str
    latest_fare: MarketFare | None
    curve_fitted_at: datetime | None
    volatility_fitted_at: datetime | None


class MarketOverview(BaseModel):
    currency: Literal["USD"] = "USD"
    routes: list[MarketRoute]


class MarketProjection(BaseModel):
    departure_date: date
    estimated_fare: float


class MarketCurve(BaseModel):
    fitted_at: datetime
    holiday_assumption: int = 0
    projections: list[MarketProjection]


class MarketVolatilityBucket(BaseModel):
    bucket_lo: int
    bucket_hi: int
    sigma: float
    n_obs: int
    shrink_w: float


class MarketVolatility(BaseModel):
    fitted_at: datetime
    buckets: list[MarketVolatilityBucket]


class MarketDetail(BaseModel):
    origin: str
    destination: str
    currency: Literal["USD"] = "USD"
    departure_date: date | None
    observations: list[MarketFare]
    curve: MarketCurve | None
    volatility: MarketVolatility | None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    phone_number: str | None = Field(default=None, max_length=20)


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=100)  # email or phone
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthenticatedUserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    balance_cents: int


class GooglePriceHistoryPoint(BaseModel):
    date: date
    amount_cents: int


class GooglePriceHistoryResponse(BaseModel):
    flight_number: str
    departure_date: date
    origin: str
    lowest_price: int | None
    price_level: Literal["low", "typical", "high", ""]
    typical_price_range: tuple[int, int] | None
    estimated_savings: int | None = None
    destination_city: str | None = None
    price_history: list[GooglePriceHistoryPoint]


class FareLockPriceRequest(BaseModel):
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: date
    flight_number: str | None = None
    fare_cents: int = Field(gt=0)


class FareLockPriceResponse(BaseModel):
    origin: str
    destination: str
    departure_date: date
    flight_number: str | None
    fare_cents: int
    fee_cents: int
    max_payout_cents: int
    protection_days: int
    currency: Literal["USD"] = "USD"


class LedgerEntryResponse(BaseModel):
    id: int
    amount_cents: int
    entry_type: str  # recharge | lock_fee | payout | withdrawal
    note: str | None
    lock_id: int | None
    created_at: datetime


class WalletTransactionResponse(BaseModel):
    user_id: int
    transactions: list[LedgerEntryResponse]


class LockPurchaseResponse(BaseModel):
    id: int
    origin: str
    destination: str
    departure_date: date
    flight_number: str | None
    strike_cents: int
    fee_cents: int
    max_payout_cents: int
    protection_days: int
    status: str
    balance_cents: int
    currency: Literal["USD"] = "USD"


class LockExerciseRequest(BaseModel):
    """Current market fare used to exercise the put (must be below strike)."""

    fare_cents: int = Field(gt=0)


class LockExerciseResponse(BaseModel):
    id: int
    status: str
    strike_cents: int
    settle_fare_cents: int
    payout_cents: int
    fee_cents: int
    balance_cents: int
    currency: Literal["USD"] = "USD"
