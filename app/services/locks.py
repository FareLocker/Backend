"""Flight-day lock settlement.

Purchase (quote -> debit wallet -> insert Lock) lives in app/routers/locks.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FareObservation, LedgerEntry, Lock, User
from app.services import sim_flights
from app.services.fastflight import search_flights
from app.services.pricer import PAYOUT_CAP

ORIGIN_TIMEZONES = {
    "ATL": "America/New_York",
    "JFK": "America/New_York",
    "LAX": "America/Los_Angeles",
    "MIA": "America/New_York",
    "ORD": "America/Chicago",
}

FareSource = Literal["provider", "stored", "sim"]

logger = logging.getLogger(__name__)


def _normalize_flight_number(value: str | None) -> str | None:
    if value is None:
        return None
    return "".join(value.upper().split()) or None


def _compute_payout_dollars(strike: float, settle_fare: float) -> float:
    """Put payoff capped at PAYOUT_CAP * K, rounded to cents."""
    difference = Decimal(str(strike)) - Decimal(str(settle_fare))
    payout_cap = Decimal(str(PAYOUT_CAP)) * Decimal(str(strike))
    return float(
        min(max(difference, Decimal(0)), payout_cap).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def _provider_fare(lock: Lock) -> float | None:
    origin, destination = lock.route.split("-")
    if lock.flight_no is not None and sim_flights.is_sim(lock.flight_no):
        return sim_flights.price_at(lock.flight_no)

    results = search_flights(
        origin=origin,
        destination=destination,
        departure_date=lock.departure_date,
        adults=1,
        seat="economy",
        currency="USD",
        flight_number=lock.flight_no,
    )
    prices: list[float] = []
    for itinerary in results:
        price = getattr(itinerary, "price", None)
        legs = list(getattr(itinerary, "flights", []) or [])
        if price is None or not isfinite(price) or price <= 0 or not legs:
            continue
        first, last = legs[0], legs[-1]
        if (
            getattr(getattr(first, "from_airport", None), "code", None) != origin
            or getattr(getattr(last, "to_airport", None), "code", None) != destination
            or tuple(getattr(getattr(first, "departure", None), "date", ()))
            != (
                lock.departure_date.year,
                lock.departure_date.month,
                lock.departure_date.day,
            )
        ):
            continue
        if lock.flight_no is not None:
            if len(legs) != 1:
                continue
            numbers = [
                getattr(first, "flight_number", ""),
                *(getattr(first, "codeshares", []) or []),
            ]
            normalized = {_normalize_flight_number(str(n)) for n in numbers}
            if lock.flight_no not in normalized:
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
            or _normalize_flight_number(row.flight_no) != lock.flight_no
        ):
            continue
        if isfinite(row.price) and row.price > 0:
            return float(row.price)
    return None


def _credit_payout(db: Session, lock: Lock, payout_dollars: float) -> None:
    if payout_dollars <= 0:
        return
    user = db.get(User, lock.user_id)
    if user is None:
        logger.error(
            "Lock %s has missing user_id=%s; payout not credited", lock.id, lock.user_id
        )
        return
    payout_cents = int(round(payout_dollars * 100))
    if payout_cents <= 0:
        return
    user.balance_cents += payout_cents
    db.add(
        LedgerEntry(
            user_id=user.id,
            amount_cents=payout_cents,
            entry_type="payout",
            note=f"FareLock payout for lock {lock.id}",
            lock_id=lock.id,
        )
    )


def settle_lock(
    db: Session, lock_id: int, *, now: datetime | None = None
) -> Lock | None:
    """Settle once on or after flight day; return None if the lock is not due."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)

    statement = select(Lock).where(Lock.id == lock_id).with_for_update(skip_locked=True)
    lock = db.scalars(statement).first()
    if lock is None or lock.status not in {"open", "pending_verification"}:
        db.rollback()
        return None

    origin = lock.route.split("-")[0]
    local_today = now.astimezone(ZoneInfo(ORIGIN_TIMEZONES[origin])).date()
    if local_today < lock.departure_date:
        db.rollback()
        return None

    fare: float | None = None
    # Past-day provider searches unlikely to return a bookable fare.
    if local_today == lock.departure_date:
        try:
            fare = _provider_fare(lock)
        except Exception:
            logger.exception("Fare search failed for lock %s", lock.id)

    if fare is None:
        fare = _stored_fare(lock, db)

    if fare is None:
        lock.status = "pending_verification"
    else:
        lock.settle_fare = fare
        lock.payout = _compute_payout_dollars(lock.K, fare)
        lock.settled_at = now
        lock.status = "exercised" if lock.payout > 0 else "expired"
        _credit_payout(db, lock, lock.payout)

    db.commit()
    db.refresh(lock)
    return lock


def _resolve_current_fare(
    lock: Lock, db: Session
) -> tuple[float | None, FareSource | None]:
    """Live provider/sim fare, then stored observation after purchase."""
    if lock.flight_no is not None and sim_flights.is_sim(lock.flight_no):
        try:
            return sim_flights.price_at(lock.flight_no), "sim"
        except Exception:
            return None, None

    try:
        provider = _provider_fare(lock)
    except Exception:
        provider = None
    if provider is not None:
        return provider, "provider"
    stored = _stored_fare(lock, db)
    if stored is not None:
        return stored, "stored"
    return None, None
