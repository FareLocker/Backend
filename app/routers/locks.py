"""Purchase FareLocks (settlement lives in app.services.locks)."""

import logging
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.config import settings
from app.db import get_db
from app.models import FareSnapshot, LedgerEntry, Lock, User
from app.routers.quotes import _normalize_flight_number, _price_farelock
from app.schemas import (
    FareSearchResultResponse,
    LockExerciseResponse,
    LockListResponse,
    LockMarkToMarketResponse,
    LockPurchaseResponse,
    LockResponse,
    NaturalFareLockQuoteRequest,
    PhotonFareLockPurchaseRequest,
    WebsiteFareLockPurchaseRequest,
)
from app.services.locks import (
    ORIGIN_TIMEZONES,
    _compute_payout_dollars,
    _credit_payout,
    _resolve_current_fare,
)
from app.services.natural_search import SearchParseError, parse_natural_farelock_quote
from app.services.pricer import PAYOUT_CAP

router = APIRouter(prefix="/locks", tags=["locks"])
DbSession = Annotated[Session, Depends(get_db)]
LockStatus = Literal["open", "exercised", "expired", "pending_verification"]
logger = logging.getLogger(__name__)


def _redact_phone(phone_number: str) -> str:
    """Keep a support trace without writing a full phone number to logs."""
    normalized = phone_number.strip()
    return f"{normalized[:2]}••••••{normalized[-4:]}" if normalized else "unknown"


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
    expected_fee_cents: int | None = None,
) -> tuple[Lock, User]:
    origin = origin.upper()
    destination = destination.upper()

    existing = db.scalar(
        select(Lock).where(
            Lock.user_id == user.id,
            Lock.route == f"{origin}-{destination}",
            Lock.departure_date == departure_date,
            Lock.flight_no == flight_no,
            Lock.status == "open",
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "lock_already_open",
                "message": "You already hold an open FareLock for this fare.",
                "lock_id": existing.id,
            },
        )

    quote = _price_farelock(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        flight_no=flight_no,
        s0_dollars=s0_dollars,
        db=db,
    )

    fee_cents = round(quote.fee * 100)
    if expected_fee_cents is not None and fee_cents != expected_fee_cents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "fee_changed",
                "message": "The FareLock fee changed. Review the new price before buying.",
                "expected_fee_cents": expected_fee_cents,
                "fee_cents": fee_cents,
            },
        )
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
            shortfall_cents=fee_cents - row.balance_cents,
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
    request: WebsiteFareLockPurchaseRequest, user: CurrentUser, db: DbSession
) -> LockPurchaseResponse:
    snapshot = db.get(FareSnapshot, request.fare_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Fare not found"
        )
    fare = FareSearchResultResponse.model_validate(snapshot.payload)
    lock, row = _purchase_farelock(
        user=user,
        origin=fare.depart.airport_code,
        destination=fare.arrive.airport_code,
        departure_date=date.fromisoformat(fare.depart.local_time[:10]),
        flight_no=_normalize_flight_number(fare.flight_number),
        s0_dollars=fare.fare_cents / 100.0,
        db=db,
        expected_fee_cents=request.expected_fee_cents,
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


def _photon_user(phone_number: str, secret: str | None, db: Session) -> User:
    """Resolve a Photon iMessage sender to its linked FareLocker account."""
    if not settings.photon_messaging_secret or secret != settings.photon_messaging_secret:
        logger.warning("farelock.photon_purchase.unauthorized")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    user = db.scalar(select(User).where(User.phone_number == phone_number.strip()))
    if user is None:
        logger.info(
            "farelock.photon_purchase.account_not_linked phone=%s",
            _redact_phone(phone_number),
        )
        raise _lock_error(
            "phone_not_linked",
            "This phone number is not linked to a FareLocker account.",
        )
    return user


@router.post("/farelock/photon-purchase", response_model=LockPurchaseResponse)
def purchase_farelock_from_photon(
    request: PhotonFareLockPurchaseRequest,
    db: DbSession,
    photon_secret: Annotated[str | None, Header(alias="X-Photon-Internal-Secret")] = None,
) -> LockPurchaseResponse:
    """Buy a FareLock for the verified sender of an inbound Photon message."""
    phone = _redact_phone(request.phone_number)
    logger.info("farelock.photon_purchase.received phone=%s", phone)
    user = _photon_user(request.phone_number, photon_secret, db)
    logger.info("farelock.photon_purchase.account_linked phone=%s user_id=%s", phone, user.id)
    try:
        parsed = parse_natural_farelock_quote(request.query)
    except SearchParseError as error:
        logger.info(
            "farelock.photon_purchase.rejected phone=%s code=%s", phone, error.code
        )
        raise _lock_error(error.code, error.message) from error

    try:
        lock, row = _purchase_farelock(
            user=user,
            origin=parsed.origin,
            destination=parsed.destination,
            departure_date=parsed.departure_date,
            flight_no=_normalize_flight_number(parsed.flight_number),
            s0_dollars=parsed.fare_paid,
            db=db,
        )
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        logger.info(
            "farelock.photon_purchase.rejected phone=%s code=%s",
            phone,
            detail.get("code", "http_error"),
        )
        raise
    logger.info(
        "farelock.photon_purchase.succeeded phone=%s user_id=%s lock_id=%s fee_cents=%s balance_cents=%s",
        phone,
        user.id,
        lock.id,
        round(lock.fee * 100),
        row.balance_cents,
    )
    return _to_purchase_response(lock, row)


def _exercisable_now(lock: Lock, *, now: datetime | None = None) -> bool:
    if lock.status != "open":
        return False
    origin = lock.route.split("-")[0]
    when = now or datetime.now(UTC)
    local_today = when.astimezone(ZoneInfo(ORIGIN_TIMEZONES.get(origin, "UTC"))).date()
    return local_today < lock.departure_date


def _mark_to_market(lock: Lock, db: Session) -> LockMarkToMarketResponse:
    strike_cents = round(lock.K * 100)
    max_payout_cents = round(PAYOUT_CAP * lock.K * 100)
    exercisable = _exercisable_now(lock)

    if lock.status != "open":
        fare_cents = None if lock.settle_fare is None else round(lock.settle_fare * 100)
        payout_cents = None if lock.payout is None else round(lock.payout * 100)
        return LockMarkToMarketResponse(
            id=lock.id,
            status=lock.status,
            strike_cents=strike_cents,
            current_fare_cents=fare_cents,
            intrinsic_payout_cents=payout_cents,
            max_payout_cents=max_payout_cents,
            in_the_money=(None if fare_cents is None else fare_cents < strike_cents),
            exercisable=False,
            fare_source=None,
        )

    fare, source = _resolve_current_fare(lock, db)
    if fare is None:
        return LockMarkToMarketResponse(
            id=lock.id,
            status=lock.status,
            strike_cents=strike_cents,
            current_fare_cents=None,
            intrinsic_payout_cents=None,
            max_payout_cents=max_payout_cents,
            in_the_money=None,
            exercisable=exercisable,
            fare_source=None,
        )

    fare_cents = round(fare * 100)
    payout = _compute_payout_dollars(lock.K, fare)
    payout_cents = round(payout * 100)
    return LockMarkToMarketResponse(
        id=lock.id,
        status=lock.status,
        strike_cents=strike_cents,
        current_fare_cents=fare_cents,
        intrinsic_payout_cents=payout_cents,
        max_payout_cents=max_payout_cents,
        in_the_money=fare_cents < strike_cents,
        exercisable=exercisable,
        fare_source=source,
    )


@router.get("/{lock_id}/mark-to-market", response_model=LockMarkToMarketResponse)
def mark_lock_to_market(
    lock_id: int, user: CurrentUser, db: DbSession
) -> LockMarkToMarketResponse:
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

    return _mark_to_market(lock, db)


def _early_exercise_lock(
    *,
    lock_id: int,
    user: User,
    db: Session,
) -> tuple[Lock, User]:
    lock = db.scalar(
        select(Lock).where(Lock.id == lock_id).with_for_update()
    )
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

    settle_fare, _source = _resolve_current_fare(lock, db)

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


def _to_lock_response(lock: Lock, db: Session) -> LockResponse:
    origin, destination = lock.route.split("-")
    fare_key = None
    if lock.flight_no is not None:
        candidate = (
            f"{origin.lower()}-{destination.lower()}-"
            f"{lock.departure_date.isoformat()}-{lock.flight_no.lower()}"
        )
        if db.get(FareSnapshot, candidate) is not None:
            fare_key = candidate
    return LockResponse(
        id=lock.id,
        origin=origin,
        destination=destination,
        departure_date=lock.departure_date,
        flight_number=lock.flight_no,
        fare_id=fare_key,
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
    return LockListResponse(locks=[_to_lock_response(lock, db) for lock in rows])


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
    return _to_lock_response(lock, db)


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
