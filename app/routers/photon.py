"""Routes for the Photon iMessage worker and its Gemini assistant.

The worker identifies a texter by their phone number, so these routes look the
account up by phone instead of a login token. They are server-to-server only:
every request must carry the shared X-Photon-Internal-Secret header, the same
check as POST /locks/farelock/photon-purchase.

The assistant's tools map onto these routes:

    my_locks          GET  /photon/users/{phone_number}/locks
    my_wallet         GET  /photon/users/{phone_number}/wallet
    LOCK (confirmed)  POST /photon/users/{phone_number}/locks

Buying is never done by the model itself: the worker calls the POST only after
the texter replies LOCK to a quote the assistant prepared.
"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Lock
from app.routers.locks import (
    _photon_user,
    _purchase_farelock,
    _redact_phone,
    _to_lock_response,
    _to_purchase_response,
)
from app.routers.quotes import _normalize_flight_number
from app.schemas import LockListResponse, LockPurchaseResponse, WalletResponse

router = APIRouter(prefix="/photon", tags=["photon"])
DbSession = Annotated[Session, Depends(get_db)]
PhotonSecret = Annotated[
    str | None,
    Header(
        alias="X-Photon-Internal-Secret",
        description="Shared secret between the API and the Photon worker.",
    ),
]

logger = logging.getLogger(__name__)


class PhotonLockPurchaseRequest(BaseModel):
    """A FareLock the texter confirmed with LOCK, with its exact flight."""

    origin: str = Field(min_length=3, max_length=3, examples=["ATL"])
    destination: str = Field(min_length=3, max_length=3, examples=["MIA"])
    departure_date: date
    flight_number: str = Field(min_length=2, max_length=10, examples=["DL 1234"])
    fare_cents: int = Field(gt=0, description="The fare the texter paid, in cents.")


@router.get(
    "/users/{phone_number}/locks",
    response_model=LockListResponse,
    summary="List a texter's FareLocks",
)
def photon_user_locks(
    phone_number: str, db: DbSession, photon_secret: PhotonSecret = None
) -> LockListResponse:
    """The FareLocks owned by the account linked to this phone number, newest
    first. Used by the assistant's my_locks tool."""
    user = _photon_user(phone_number, photon_secret, db)
    rows = db.scalars(
        select(Lock)
        .where(Lock.user_id == user.id)
        .order_by(Lock.bought_at.desc(), Lock.id.desc())
    ).all()
    return LockListResponse(locks=[_to_lock_response(lock, db) for lock in rows])


@router.get(
    "/users/{phone_number}/wallet",
    response_model=WalletResponse,
    summary="Get a texter's wallet balance",
)
def photon_user_wallet(
    phone_number: str, db: DbSession, photon_secret: PhotonSecret = None
) -> WalletResponse:
    """The wallet balance of the account linked to this phone number. Used by
    the assistant's my_wallet tool."""
    user = _photon_user(phone_number, photon_secret, db)
    return WalletResponse(user_id=user.id, balance_cents=user.balance_cents)


@router.post(
    "/users/{phone_number}/locks",
    response_model=LockPurchaseResponse,
    summary="Buy a FareLock the texter confirmed",
)
def photon_user_purchase_lock(
    phone_number: str,
    request: PhotonLockPurchaseRequest,
    db: DbSession,
    photon_secret: PhotonSecret = None,
) -> LockPurchaseResponse:
    """Buy a FareLock for the account linked to this phone number, paid from
    its wallet. The worker calls this only after the texter replies LOCK to a
    quote the assistant prepared. Errors are 422s with detail.code, e.g.
    insufficient_funds, unsupported_route or outside_quote_window."""
    phone = _redact_phone(phone_number)
    user = _photon_user(phone_number, photon_secret, db)
    try:
        lock, row = _purchase_farelock(
            user=user,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            flight_no=_normalize_flight_number(request.flight_number),
            s0_dollars=request.fare_cents / 100,
            db=db,
        )
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        logger.info(
            "farelock.photon_lock.rejected phone=%s code=%s",
            phone,
            detail.get("code", "http_error"),
        )
        raise
    logger.info(
        "farelock.photon_lock.succeeded phone=%s user_id=%s lock_id=%s",
        phone,
        user.id,
        lock.id,
    )
    return _to_purchase_response(lock, row)
