"""Trusted lock creation and automatic flight-day settlement."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
from math import isfinite
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import ROUTES
from app.models import FareObservation, Lock
from app.services.fastflight import search_flights


ORIGIN_TIMEZONES = {
    "ATL": "America/New_York",
    "JFK": "America/New_York",
    "LAX": "America/Los_Angeles",
    "MIA": "America/New_York",
    "ORD": "America/Chicago",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PricedLockInput:
    """A pricing snapshot supplied by trusted application code."""

    origin: str
    destination: str
    departure_date: date
    strike: float
    fee: float
    model_value: float
    lam: float
    sigma_used: float
    flight_no: str | None = None


def _flight_number(value: str | None) -> str | None:
    if value is None:
        return None
    number = "".join(value.upper().split())
    if not number or not number.isalnum() or len(number) > 10:
        raise ValueError("Flight number must be 1-10 alphanumeric characters")
    return number


def _route(origin: str, destination: str) -> tuple[str, str]:
    route = (origin.strip().upper(), destination.strip().upper())
    if route not in ROUTES:
        raise ValueError("Unsupported route")
    return route


def create_lock(
    db: Session, priced: PricedLockInput, *, bought_at: datetime | None = None
) -> Lock:
    """Persist a trusted USD economy, one-adult, one-way lock snapshot."""
    origin, destination = _route(priced.origin, priced.destination)
    bought_at = bought_at or datetime.now(timezone.utc)
    if bought_at.tzinfo is None or bought_at.utcoffset() is None:
        raise ValueError("bought_at must be timezone-aware")
    bought_at = bought_at.astimezone(timezone.utc)
    days = (
        priced.departure_date
        - bought_at.astimezone(ZoneInfo(ORIGIN_TIMEZONES[origin])).date()
    ).days
    if days < 1:
        raise ValueError("Departure date must be after the purchase date")
    for name in ("strike", "fee", "model_value", "lam", "sigma_used"):
        value = getattr(priced, name)
        if not isfinite(value) or value < 0 or (name == "strike" and value == 0):
            raise ValueError(f"{name} must be a finite nonnegative amount")
    lock = Lock(
        route=f"{origin}-{destination}",
        flight_no=_flight_number(priced.flight_no),
        departure_date=priced.departure_date,
        K=priced.strike,
        T_days=days,
        d0=days,
        model_value=priced.model_value,
        fee=priced.fee,
        lam=priced.lam,
        sigma_used=priced.sigma_used,
        status="open",
        bought_at=bought_at,
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)
    return lock


def _provider_fare(lock: Lock) -> float | None:
    origin, destination = lock.route.split("-")
    results = search_flights(
        origin=origin,
        destination=destination,
        departure_date=lock.departure_date,
        adults=1,
        seat="economy",
        currency="USD",
    )
    prices = []
    for itinerary in results:
        price = getattr(itinerary, "price", None)
        legs = getattr(itinerary, "flights", [])
        if price is None or not isfinite(price) or price <= 0 or not legs:
            continue
        first = legs[0]
        last = legs[-1]
        if (
            getattr(getattr(first, "from_airport", None), "code", None) != origin
            or getattr(getattr(last, "to_airport", None), "code", None) != destination
            or tuple(getattr(getattr(first, "departure", None), "date", ()))
            != (lock.departure_date.year, lock.departure_date.month, lock.departure_date.day)
        ):
            continue
        if lock.flight_no is not None:
            if len(legs) != 1:
                continue
            numbers = [getattr(first, "flight_number", ""), *(getattr(first, "codeshares", []) or [])]
            if lock.flight_no not in {"".join(str(n).upper().split()) for n in numbers}:
                continue
        prices.append(float(price))
    return min(prices) if prices else None


def _stored_fare(lock: Lock, db: Session) -> float | None:
    origin, destination = lock.route.split("-")
    rows = db.scalars(
        select(FareObservation)
        .where(
            FareObservation.origin == origin,
            FareObservation.dest == destination,
            FareObservation.departure_date == lock.departure_date,
            FareObservation.cabin == "economy",
            FareObservation.queried_at > lock.bought_at,
        )
        .order_by(FareObservation.queried_at.desc(), FareObservation.id.desc())
    )
    for row in rows:
        if lock.flight_no is not None and (
            row.flight_no is None
            or "".join(row.flight_no.upper().split()) != lock.flight_no
        ):
            continue
        if isfinite(row.price) and row.price > 0:
            return float(row.price)
    return None


def settle_lock(db: Session, lock_id: int, *, now: datetime | None = None) -> Lock | None:
    """Settle once on or after flight day; return None if the lock is not due."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    statement = select(Lock).where(Lock.id == lock_id).with_for_update(skip_locked=True)
    lock = db.scalars(statement).first()
    if lock is None or lock.status not in {"open", "pending_verification"}:
        db.rollback()
        return None
    origin = lock.route.split("-")[0]
    if now.astimezone(ZoneInfo(ORIGIN_TIMEZONES[origin])).date() < lock.departure_date:
        db.rollback()
        return None

    fare = None
    # Past-day provider searches are unlikely to return a bookable fare.
    if now.astimezone(ZoneInfo(ORIGIN_TIMEZONES[origin])).date() == lock.departure_date:
        try:
            fare = _provider_fare(lock)
        except Exception:
            # A provider outage must not prevent the stored-fare fallback.
            logger.exception("Fare search failed for lock %s", lock.id)
    if fare is None:
        fare = _stored_fare(lock, db)
    if fare is None:
        lock.status = "pending_verification"
    else:
        lock.settle_fare = fare
        difference = Decimal(str(fare)) - Decimal(str(lock.K))
        lock.payout = float(max(difference, Decimal(0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        lock.settled_at = now
        lock.status = "exercised" if lock.payout > 0 else "expired"
    db.commit()
    db.refresh(lock)
    return lock
