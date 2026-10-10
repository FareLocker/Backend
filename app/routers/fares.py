"""Normalized fare APIs for the FareLocker web client."""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import ROUTES
from app.db import get_db
from app.models import FareObservation, FareSnapshot
from app.schemas import (
    FareDetailResponse,
    FareEndpointResponse,
    FareHistoryPoint,
    FareLockQuoteSummary,
    FareSearchResponse,
    FareSearchResultResponse,
    FareTrendResponse,
    GooglePriceHistoryResponse,
    GooglePriceHistoryPoint,
    NaturalFlightSearchRequest,
)
from app.services.fastflight import fetch_price_history, search_flights
from app.services.natural_search import SearchParseError, parse_natural_search
from app.services.pricer import quote_lock

router = APIRouter(prefix="/fares", tags=["fares"])
DbSession = Annotated[Session, Depends(get_db)]
MAX_QUOTE_DAYS = 30


def _flight_number(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "".join(value.upper().split())
    return normalized or None


def fare_id(
    origin: str, destination: str, departure: date, flight_no: str | None
) -> str:
    """Stable URL-safe key; provider offer IDs are deliberately not exposed."""
    flight = _flight_number(flight_no) or "unknown"
    return f"{origin.lower()}-{destination.lower()}-{departure.isoformat()}-{flight.lower()}"


def _parse_fare_id(fare_key: str) -> tuple[str, str, date, str | None]:
    parts = fare_key.split("-")
    if len(parts) < 6:
        raise ValueError("invalid fare key")
    origin, destination = parts[0].upper(), parts[1].upper()
    departure = date.fromisoformat("-".join(parts[2:5]))
    flight_no = _flight_number("-".join(parts[5:]))
    return origin, destination, departure, None if flight_no == "UNKNOWN" else flight_no


def _history(
    db: Session,
    *,
    origin: str,
    destination: str,
    departure: date,
    flight_no: str | None,
) -> list[FareHistoryPoint]:
    """Return one latest USD economy price per observed calendar day."""
    rows = db.scalars(
        select(FareObservation)
        .where(
            FareObservation.origin == origin,
            FareObservation.dest == destination,
            FareObservation.departure_date == departure,
            FareObservation.cabin == "economy",
        )
        .order_by(FareObservation.queried_at.asc(), FareObservation.id.asc())
    ).all()
    daily: dict[date, FareObservation] = {}
    for row in rows:
        if _flight_number(row.flight_no) == flight_no:
            daily[row.queried_at.date()] = row
    return [
        FareHistoryPoint(date=day, amount_cents=round(row.price * 100))
        for day, row in sorted(daily.items())
    ]


def _trend(history: list[FareHistoryPoint]) -> FareTrendResponse:
    points = [point.amount_cents for point in history[-30:]]
    if len(points) < 2:
        return FareTrendResponse(points_cents=points, change_percent=0)
    baseline = points[max(0, len(points) - 8)]
    change = 0 if baseline <= 0 else ((points[-1] - baseline) / baseline) * 100
    return FareTrendResponse(points_cents=points, change_percent=round(change, 1))


def _local_time(value: object) -> str:
    """Format the provider's wall-clock SimpleDatetime without timezone conversion."""
    day = getattr(value, "date", None)
    clock = getattr(value, "time", None)
    if not isinstance(day, tuple) or not isinstance(clock, tuple):
        return ""
    return f"{day[0]:04d}-{day[1]:02d}-{day[2]:02d}T{clock[0]:02d}:{clock[1]:02d}"


def _endpoint(airport: object, local_datetime: object) -> FareEndpointResponse:
    code = str(getattr(airport, "code", ""))
    # Fast Flights gives airport names but not city names. The code is the
    # temporary display label until an airport metadata source is added.
    return FareEndpointResponse(
        airport_code=code,
        airport_name=str(getattr(airport, "name", code)),
        city=code,
        local_time=_local_time(local_datetime),
    )


def _lock_quote(
    db: Session,
    *,
    origin: str,
    destination: str,
    departure: date,
    flight_no: str | None,
    price: float,
) -> FareLockQuoteSummary | None:
    protection_days = (departure - date.today()).days
    if (
        origin,
        destination,
    ) not in ROUTES or not 1 <= protection_days <= MAX_QUOTE_DAYS:
        return None
    try:
        quote = quote_lock(
            route=(origin, destination),
            flight_no=flight_no,
            _departure_date=departure,
            S0=price,
            d0=protection_days,
            dow=departure.weekday(),
            db=db,
        )
    except ValueError:
        return None
    return FareLockQuoteSummary(fee_cents=round(quote.fee * 100))


def _normalize_itinerary(
    itinerary: object, departure: date, db: Session
) -> FareSearchResultResponse | None:
    price = getattr(itinerary, "price", None)
    flights = list(getattr(itinerary, "flights", []) or [])
    if not isinstance(price, (int, float)) or price <= 0 or not flights:
        return None
    first, last = flights[0], flights[-1]
    origin = str(getattr(getattr(first, "from_airport", None), "code", "")).upper()
    destination = str(getattr(getattr(last, "to_airport", None), "code", "")).upper()
    flight_no = _flight_number(getattr(first, "flight_number", None))
    if not origin or not destination or flight_no is None:
        return None
    history = _history(
        db,
        origin=origin,
        destination=destination,
        departure=departure,
        flight_no=flight_no,
    )
    return FareSearchResultResponse(
        id=fare_id(origin, destination, departure, flight_no),
        airline_name=", ".join(getattr(itinerary, "airlines", []) or [])
        or "Unknown airline",
        flight_number=flight_no,
        cabin_class="Economy",
        stop_airports=[
            str(getattr(getattr(leg, "to_airport", None), "code", ""))
            for leg in flights[:-1]
            if getattr(getattr(leg, "to_airport", None), "code", None)
        ],
        depart=_endpoint(
            getattr(first, "from_airport", None), getattr(first, "departure", None)
        ),
        arrive=_endpoint(
            getattr(last, "to_airport", None), getattr(last, "arrival", None)
        ),
        duration_minutes=sum(int(getattr(leg, "duration", 0) or 0) for leg in flights),
        fare_cents=round(float(price) * 100),
        lock=_lock_quote(
            db,
            origin=origin,
            destination=destination,
            departure=departure,
            flight_no=flight_no,
            price=float(price),
        ),
        trend=_trend(history),
    )


def _store_snapshot(db: Session, fare: FareSearchResultResponse) -> None:
    snapshot = db.get(FareSnapshot, fare.id)
    if snapshot is None:
        snapshot = FareSnapshot(id=fare.id, payload=fare.model_dump())
        db.add(snapshot)
    else:
        snapshot.payload = fare.model_dump()
        snapshot.updated_at = datetime.now(UTC)


def _deduplicate_results(results: list[FareSearchResultResponse]) -> list[FareSearchResultResponse]:
    """Keep one provider offer for every stable fare ID before snapshotting it."""
    unique: dict[str, FareSearchResultResponse] = {}
    for result in results:
        existing = unique.get(result.id)
        # Providers can return the same first flight in multiple itineraries.
        # The ID intentionally represents that flight, so retain the cheapest offer.
        if existing is None or result.fare_cents < existing.fare_cents:
            unique[result.id] = result
    return list(unique.values())


# Look for search issue here
@router.post("/search", response_model=FareSearchResponse)
async def search_fares(
    request: NaturalFlightSearchRequest, db: DbSession
) -> FareSearchResponse:
    """Search live itineraries and enrich each result with local price history."""
    try:
        parsed = parse_natural_search(request.query)
    except SearchParseError as error:
        raise HTTPException(
            status_code=422, detail={"code": error.code, "message": error.message}
        ) from error
    try:
        itineraries = await run_in_threadpool(
            search_flights, **parsed.model_dump(exclude={"context"})
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_search", "message": str(error)},
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "search_unavailable",
                "message": "Flight search is temporarily unavailable. Please try again later.",
            },
        ) from error

    normalized_results = [
        normalized for itinerary in itineraries
        if (normalized := _normalize_itinerary(itinerary, parsed.departure_date, db)) is not None
    ]
    results = _deduplicate_results(normalized_results)
    for result in results:
        _store_snapshot(db, result)
    db.commit()
    return FareSearchResponse(results=results)


@router.get("/price-history", response_model=GooglePriceHistoryResponse)
async def price_history(
    flight_number: str = Query(..., examples=["F9 3288"]),
    departure_date: date = Query(...),
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
) -> GooglePriceHistoryResponse:
    try:
        insights = await run_in_threadpool(
            fetch_price_history,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            destination=destination,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502, detail="Price history unavailable"
        ) from error

    return GooglePriceHistoryResponse(
        flight_number=flight_number,
        departure_date=departure_date,
        origin=origin.upper(),
        lowest_price=insights.lowest_price,
        price_level=insights.price_level or "",
        typical_price_range=insights.typical_price_range,
        estimated_savings=insights.estimated_savings,
        destination_city=insights.destination_city,
        price_history=[
            GooglePriceHistoryPoint(
                date=date.fromisoformat(point.iso_date),
                amount_cents=int(point.price) * 100,
            )
            for point in insights.price_history
        ],
    )


@router.get("/{fare_key}", response_model=FareDetailResponse)
def get_fare_detail(fare_key: str, db: DbSession) -> FareDetailResponse:
    """Return stored daily history for a fare list item's deterministic ID."""
    snapshot = db.get(FareSnapshot, fare_key)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Fare not found")
    fare = FareSearchResultResponse.model_validate(snapshot.payload)
    try:
        origin, destination, departure, flight_no = _parse_fare_id(fare.id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Fare not found") from error
    history = _history(
        db,
        origin=origin,
        destination=destination,
        departure=departure,
        flight_no=flight_no,
    )
    return FareDetailResponse(fare=fare, history=history)
