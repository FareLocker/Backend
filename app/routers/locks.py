"""Purchase FareLocks (settlement lives in app.services.locks)."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import LedgerEntry, Lock, User
from app.routers.quotes import _normalize_flight_number, _price_farelock
from app.schemas import (
    FareLockPriceRequest,
    LockExerciseResponse,
    LockListResponse,
    LockPurchaseResponse,
    LockResponse,
    NaturalFareLockQuoteRequest,
)
from app.services.locks import (
    ORIGIN_TIMEZONES,
    _compute_payout_dollars,
    _credit_payout,
    _provider_fare,
    _stored_fare,
)
from app.services.natural_search import SearchParseError, parse_natural_farelock_quote
from app.services.pricer import PAYOUT_CAP

router = APIRouter(prefix="/locks", tags=["locks"])
DbSession = Annotated[Session, Depends(get_db)]
LockStatus = Literal["open", "exercised", "expired", "pending_verification"]


def _lock_error(code: str, message: str, **context: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message, **context},
    )


def _purchase_farelock(
    *,
    user: User,
    origin: str,
    destination: str,
    departure_date: date,
    flight_no: str | None,
    s0_dollars: float,
    db: Session,
) -> tuple[Lock, User]:
    origin = origin.upper()
    destination = destination.upper()

    quote = _price_farelock(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        flight_no=flight_no,
        s0_dollars=s0_dollars,
        db=db,
    )

    fee_cents = round(quote.fee * 100)
    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if row.balance_cents < fee_cents:
        raise _lock_error(
            "insufficient_funds",
            "Wallet balance is too low to purchase this FareLock.",
            balance_cents=row.balance_cents,
            fee_cents=fee_cents,
        )

    bought_at = datetime.now(UTC)
    tz = ZoneInfo(ORIGIN_TIMEZONES.get(origin, "UTC"))
    d0 = (departure_date - bought_at.astimezone(tz).date()).days
    if d0 < 1:
        raise _lock_error(
            "departure_too_soon", "FareLock requires a departure date after today."
        )

    lock = Lock(
        user_id=row.id,
        route=f"{origin}-{destination}",
        flight_no=flight_no,
        departure_date=departure_date,
        K=quote.K,
        T_days=d0,
        d0=d0,
        model_value=quote.V_model,
        fee=quote.fee,
        lam=quote.lam,
        sigma_used=quote.sigma_used,
        status="open",
        bought_at=bought_at,
    )
    db.add(lock)
    db.flush()
    row.balance_cents -= fee_cents
    db.add(
        LedgerEntry(
            user_id=row.id,
            amount_cents=-fee_cents,
            entry_type="lock_fee",
            note=f"FareLock fee for lock {lock.id}",
            lock_id=lock.id,
        )
    )
    db.commit()
    db.refresh(lock)
    db.refresh(row)
    return lock, row


def _to_purchase_response(lock: Lock, user: User) -> LockPurchaseResponse:
    origin, destination = lock.route.split("-")
    return LockPurchaseResponse(
        id=lock.id,
        origin=origin,
        destination=destination,
        departure_date=lock.departure_date,
        flight_number=lock.flight_no,
        strike_cents=round(lock.K * 100),
        fee_cents=round(lock.fee * 100),
        max_payout_cents=round(PAYOUT_CAP * lock.K * 100),
        protection_days=lock.T_days,
        status=lock.status,
        balance_cents=user.balance_cents,
    )


@router.post("/farelock/purchase", response_model=LockPurchaseResponse)
def purchase_farelock_structured(
    request: FareLockPriceRequest, user: CurrentUser, db: DbSession
) -> LockPurchaseResponse:
    lock, row = _purchase_farelock(
        user=user,
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        flight_no=_normalize_flight_number(request.flight_number),
        s0_dollars=request.fare_cents / 100.0,
        db=db,
    )
    return _to_purchase_response(lock, row)


@router.post("/farelock", response_model=LockPurchaseResponse)
def purchase_farelock_natural(
    request: NaturalFareLockQuoteRequest, user: CurrentUser, db: DbSession
) -> LockPurchaseResponse:
    try:
        parsed = parse_natural_farelock_quote(request.query)
    except SearchParseError as error:
        raise _lock_error(error.code, error.message) from error

    lock, row = _purchase_farelock(
        user=user,
        origin=parsed.origin,
        destination=parsed.destination,
        departure_date=parsed.departure_date,
        flight_no=_normalize_flight_number(parsed.flight_number),
        s0_dollars=parsed.fare_paid,
        db=db,
    )
    return _to_purchase_response(lock, row)


def _early_exercise_lock(
    *,
    lock_id: int,
    user: User,
    db: Session,
) -> tuple[Lock, User]:
    lock = db.get(Lock, lock_id)
    if lock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lock not found"
        )

    if lock.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot exercise another user's lock",
        )

    if lock.status != "open":
        raise _lock_error(
            "lock_not_open",
            "Only an open FareLock can be exercised.",
            status=lock.status,
        )

    origin = lock.route.split("-")[0]
    now = datetime.now(UTC)
    local_today = now.astimezone(ZoneInfo(ORIGIN_TIMEZONES.get(origin, "UTC"))).date()
    if local_today >= lock.departure_date:
        raise _lock_error(
            "past_exercise_window",
            "Early exercise is only available before the departure date.",
            departure_date=lock.departure_date.isoformat(),
        )

    try:
        settle_fare = _provider_fare(lock)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not fetch current fare for this lock",
        ) from error

    if settle_fare is None:
        settle_fare = _stored_fare(lock, db)

    if settle_fare is None:
        raise _lock_error(
            "fare_unavailable",
            "No current fare found for this flight; try again shortly.",
        )

    fare_cents = round(settle_fare * 100)

    strike_cents = round(lock.K * 100)
    if fare_cents >= strike_cents:
        raise _lock_error(
            "fare_not_below_strike",
            "Early exercise requires the current fare to be below your strike.",
            fare_cents=fare_cents,
            strike_cents=strike_cents,
        )

    settle_fare = fare_cents / 100.0
    payout = _compute_payout_dollars(lock.K, settle_fare)

    lock.settle_fare = settle_fare
    lock.payout = payout
    lock.settled_at = now
    lock.status = "exercised"
    _credit_payout(db, lock, payout)

    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    db.commit()
    db.refresh(lock)
    db.refresh(row)
    return lock, row


def _to_lock_response(lock: Lock) -> LockResponse:
    origin, destination = lock.route.split("-")
    return LockResponse(
        id=lock.id,
        origin=origin,
        destination=destination,
        departure_date=lock.departure_date,
        flight_number=lock.flight_no,
        strike_cents=round(lock.K * 100),
        fee_cents=round(lock.fee * 100),
        max_payout_cents=round(PAYOUT_CAP * lock.K * 100),
        protection_days=lock.T_days,
        status=lock.status,
        bought_at=lock.bought_at,
        settled_at=lock.settled_at,
        settle_fare_cents=(
            None if lock.settle_fare is None else round(lock.settle_fare * 100)
        ),
        payout_cents=None if lock.payout is None else round(lock.payout * 100),
    )


@router.get("", response_model=LockListResponse)
def list_locks(
    user: CurrentUser,
    db: DbSession,
    status_filter: LockStatus | None = Query(default=None, alias="status"),
) -> LockListResponse:
    stmt = (
        select(Lock)
        .where(Lock.user_id == user.id)
        .order_by(Lock.bought_at.desc(), Lock.id.desc())
    )
    if status_filter is not None:
        stmt = stmt.where(Lock.status == status_filter)
    rows = db.scalars(stmt).all()
    return LockListResponse(locks=[_to_lock_response(lock) for lock in rows])


@router.get("/{lock_id}", response_model=LockResponse)
def get_lock(lock_id: int, user: CurrentUser, db: DbSession) -> LockResponse:
    lock = db.get(Lock, lock_id)
    if lock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lock not found"
        )
    if lock.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's lock",
        )
    return _to_lock_response(lock)


@router.post("/{lock_id}/exercise", response_model=LockExerciseResponse)
def early_exercise_lock(
    lock_id: int, user: CurrentUser, db: DbSession
) -> LockExerciseResponse:
    lock, row = _early_exercise_lock(lock_id=lock_id, user=user, db=db)

    return LockExerciseResponse(
        id=lock.id,
        status=lock.status,
        strike_cents=round(lock.K * 100),
        settle_fare_cents=round((lock.settle_fare or 0) * 100),
        payout_cents=round((lock.payout or 0) * 100),
        fee_cents=round(lock.fee * 100),
        balance_cents=row.balance_cents,
    )
