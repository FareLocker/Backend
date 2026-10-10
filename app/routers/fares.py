"""Read fare detail and observation history from the fare store."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import ROUTES
from app.db import get_db
from app.models import FareObservation
from app.schemas import FareDetailResponse, FareHistoryPoint

router = APIRouter(prefix="/fares", tags=["fares"])
DbSession = Annotated[Session, Depends(get_db)]


def fare_id(origin: str, destination: str, departure: date, flight_no: str | None) -> str:
    """Stable itinerary key shared by fare search and detail endpoints."""
    flight = "unknown" if not flight_no else "".join(flight_no.upper().split())
    return f"{origin.lower()}-{destination.lower()}-{departure.isoformat()}-{flight.lower()}"


@router.get("/{fare_key}", response_model=FareDetailResponse)
def get_fare_detail(fare_key: str, db: DbSession) -> FareDetailResponse:
    """Return latest stored observation and chronological price history.

    The key format is `{origin}-{destination}-{YYYY-MM-DD}-{flight_number}`;
    use `unknown` for observations without a flight number. This intentionally
    exposes only fields available in FareObservation; schedule/airport metadata
    can be added when the search provider contract is normalized.
    """
    parts = fare_key.split("-")
    if len(parts) < 5:
        raise HTTPException(status_code=404, detail="Fare not found")
    origin, destination = parts[0], parts[1]
    date_text = "-".join(parts[2:5])
    flight_text = "-".join(parts[5:]) or "unknown"
    try:
        departure = date.fromisoformat(date_text)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Fare not found") from error

    origin, destination = origin.upper(), destination.upper()
    if (origin, destination) not in ROUTES:
        raise HTTPException(status_code=404, detail="Fare not found")
    flight_no = None if flight_text == "unknown" else flight_text.upper()
    statement = select(FareObservation).where(
        FareObservation.origin == origin,
        FareObservation.dest == destination,
        FareObservation.departure_date == departure,
        FareObservation.cabin == "economy",
    )
    if flight_no is None:
        statement = statement.where(FareObservation.flight_no.is_(None))
    else:
        statement = statement.where(FareObservation.flight_no == flight_no)
    rows = list(
        db.scalars(
            statement.order_by(FareObservation.queried_at.asc(), FareObservation.id.asc())
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Fare not found")

    latest = rows[-1]
    return FareDetailResponse(
        id=fare_id(origin, destination, departure, latest.flight_no),
        origin=origin,
        destination=destination,
        departure_date=departure,
        flight_number=latest.flight_no,
        airline=latest.airline,
        cabin=latest.cabin,
        latest_fare=latest.price,
        history=[FareHistoryPoint(queried_at=row.queried_at, price=row.price) for row in rows],
    )
