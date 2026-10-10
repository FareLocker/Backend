from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.constants import ROUTES
from app.db import get_db
from app.schemas import FareLockQuoteResponse, NaturalFareLockQuoteRequest
from app.services.natural_search import SearchParseError, parse_natural_farelock_quote
from app.services.pricer import PAYOUT_CAP, quote_lock


router = APIRouter(prefix="/quotes", tags=["quotes"])
DbSession = Annotated[Session, Depends(get_db)]
MAX_QUOTE_DAYS = 30


def _quote_error(code: str, message: str, **context: object) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": code, "message": message, **context},
    )


@router.post("/farelock", response_model=FareLockQuoteResponse)
def farelock_quote(
    request: NaturalFareLockQuoteRequest, db: DbSession
) -> FareLockQuoteResponse:
    try:
        parsed = parse_natural_farelock_quote(request.query)
    except SearchParseError as error:
        raise _quote_error(error.code, error.message) from error

    route = (parsed.origin, parsed.destination)
    if route not in ROUTES:
        raise _quote_error(
            "unsupported_route",
            f"FareLock pricing is not available for {parsed.origin} to {parsed.destination} yet.",
        )

    protection_days = (parsed.departure_date - date.today()).days
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
        quote = quote_lock(
            route=route,
            flight_no=parsed.flight_number,
            _departure_date=parsed.departure_date,
            S0=parsed.fare_paid,
            d0=protection_days,
            dow=parsed.departure_date.weekday(),
            db=db,
        )
    except ValueError as error:
        message = str(error)
        if message.startswith("no curve fit"):
            raise _quote_error(
                "pricing_unavailable",
                (
                    f"I can’t quote FareLock for {parsed.origin} to {parsed.destination} yet "
                    "because we don’t have enough historical fare data to calculate a reliable fee. "
                    "Try another supported route or check back after more fares have been collected."
                ),
                route=f"{parsed.origin}-{parsed.destination}",
                reason="historical_fare_data_unavailable",
                minimum_observations=30,
            ) from error
        raise _quote_error("pricing_failed", "FareLock could not price this request.") from error

    return FareLockQuoteResponse(
        origin=parsed.origin,
        destination=parsed.destination,
        departure_date=parsed.departure_date,
        fare_paid=round(quote.K, 2),
        flight_number=parsed.flight_number,
        fee=round(quote.fee, 2),
        max_potential_payout=round(PAYOUT_CAP * quote.K, 2),
        protection_days=quote.T_days,
    )
