from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.constants import ROUTES
from app.db import get_db
from app.schemas import (
    FareLockPriceRequest,
    FareLockPriceResponse,
    FareLockQuoteResponse,
    NaturalFareLockQuoteRequest,
)
from app.services.natural_search import SearchParseError, parse_natural_farelock_quote
from app.services.pricer import PAYOUT_CAP, Quote, quote_lock

router = APIRouter(prefix="/quotes", tags=["quotes"])
DbSession = Annotated[Session, Depends(get_db)]
MAX_QUOTE_DAYS = 30


def _quote_error(code: str, message: str, **context: object) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": code, "message": message, **context},
    )


def _normalize_flight_number(flight_number: str | None) -> str | None:
    if flight_number is None:
        return None
    normalized = "".join(flight_number.upper().split())
    return normalized or None


def _price_farelock(
    *,
    origin: str,
    destination: str,
    departure_date: date,
    flight_no: str | None,
    s0_dollars: float,
    db: Session,
) -> Quote:
    """Shared FareLock pricing: route/window checks + quote_lock."""
    origin = origin.upper()
    destination = destination.upper()
    route = (origin, destination)

    if route not in ROUTES:
        raise _quote_error(
            "unsupported_route",
            f"FareLock pricing is not available for {origin} to {destination} yet.",
        )

    protection_days = (departure_date - date.today()).days
    if protection_days < 1:
        raise _quote_error(
            "departure_too_soon",
            "FareLock requires a departure date after today.",
        )
    if protection_days > MAX_QUOTE_DAYS:
        raise _quote_error(
            "outside_quote_window",
            f"FareLock quotes are available within {MAX_QUOTE_DAYS} days of departure.",
        )

    try:
        return quote_lock(
            route=route,
            flight_no=flight_no,
            _departure_date=departure_date,
            S0=s0_dollars,
            d0=protection_days,
            dow=departure_date.weekday(),
            db=db,
        )
    except ValueError as error:
        message = str(error)
        if message.startswith("no curve fit"):
            raise _quote_error(
                "pricing_unavailable",
                (
                    f"I can’t quote FareLock for {origin} to {destination} yet "
                    "because we don’t have enough historical fare data to calculate a reliable fee. "
                    "Try another supported route or check back after more fares have been collected."
                ),
                route=f"{origin}-{destination}",
                reason="historical_fare_data_unavailable",
                minimum_observations=30,
            ) from error
        raise _quote_error(
            "pricing_failed", "FareLock could not price this request."
        ) from error


@router.post("/farelock", response_model=FareLockQuoteResponse)
def farelock_quote(
    request: NaturalFareLockQuoteRequest, db: DbSession
) -> FareLockQuoteResponse:
    try:
        parsed = parse_natural_farelock_quote(request.query)
    except SearchParseError as error:
        raise _quote_error(error.code, error.message) from error

    quote = _price_farelock(
        origin=parsed.origin,
        destination=parsed.destination,
        departure_date=parsed.departure_date,
        flight_no=parsed.flight_number,
        s0_dollars=parsed.fare_paid,
        db=db,
    )
    return FareLockQuoteResponse(
        origin=parsed.origin.upper(),
        destination=parsed.destination.upper(),
        departure_date=parsed.departure_date,
        fare_paid=round(quote.K, 2),
        flight_number=parsed.flight_number,
        fee=round(quote.fee, 2),
        max_potential_payout=round(PAYOUT_CAP * quote.K, 2),
        protection_days=quote.T_days,
    )


@router.post("/farelock/price", response_model=FareLockPriceResponse)
def farelock_price(
    request: FareLockPriceRequest, db: DbSession
) -> FareLockPriceResponse:
    origin = request.origin.upper()
    destination = request.destination.upper()
    flight_no = _normalize_flight_number(request.flight_number)
    strike_cents = (
        request.strike_cents
        if request.strike_cents is not None
        else request.fare_cents
    )

    quote = _price_farelock(
        origin=origin,
        destination=destination,
        departure_date=request.departure_date,
        flight_no=flight_no,
        s0_dollars=strike_cents / 100.0,
        db=db,
    )
    return FareLockPriceResponse(
        origin=origin,
        destination=destination,
        departure_date=request.departure_date,
        flight_number=flight_no,
        fare_cents=request.fare_cents,
        strike_cents=strike_cents,
        fee_cents=round(quote.fee * 100),
        max_payout_cents=round(PAYOUT_CAP * quote.K * 100),
        protection_days=quote.T_days,
    )
