import json
import logging
import re
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, create_access_token, hash_password, verify_password
from app.config import settings
from app.db import get_db
from app.models import LedgerEntry, Lock, User
from app.schemas import (
    AuthenticatedUserResponse,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)


def _normalize_phone_number(phone_number: str) -> str | None:
    """Return an E.164 number for the U.S./Canada formats accepted by signup.

    International numbers must already be supplied in E.164 form. A country
    cannot be inferred reliably from an unprefixed international number.
    """
    value = phone_number.strip()
    if re.fullmatch(r"\+[1-9]\d{7,14}", value):
        return value

    # Let people enter familiar North American formats such as
    # "(404) 555-0123" or "1-404-555-0123".
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def _send_signup_welcome(
    phone_number: str, first_name: str, last_name: str, email: str
) -> None:
    """Ask the Photon worker to send the post-signup iMessage.

    This is deliberately best-effort: the user account has already been
    committed, and a Photon outage must not turn a successful signup into an
    error.
    """
    if not settings.photon_messaging_url or not settings.photon_messaging_secret:
        logger.warning(
            "Skipping signup welcome: Photon messaging URL or shared secret is not configured"
        )
        return

    request = Request(
        f"{settings.photon_messaging_url.rstrip('/')}/send-welcome",
        data=json.dumps(
            {
                "phone_number": phone_number,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Photon-Internal-Secret": settings.photon_messaging_secret,
        },
        method="POST",
    )
    try:
        with urlopen(
            request, timeout=settings.photon_messaging_timeout_seconds
        ) as response:
            if response.status >= 300:
                raise RuntimeError(f"Photon returned HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as error:
        logger.warning("Could not send signup welcome message: %s", error)


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _user_by_phone(db: Session, phone_number: str) -> User | None:
    return db.scalar(select(User).where(User.phone_number == phone_number))


def _to_user_response(user: User) -> AuthenticatedUserResponse:
    return AuthenticatedUserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        balance_cents=user.balance_cents,
    )


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
def register(
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> TokenResponse:
    email = body.email.lower().strip()
    if _user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    phone = None
    if body.phone_number:
        phone = _normalize_phone_number(body.phone_number)
        if phone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Enter a 10-digit U.S./Canada phone number or an "
                    "international number in E.164 format (for example, +15551234567)"
                ),
            )

    if phone is not None and _user_by_phone(db, phone) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number already registered",
        )

    user = User(
        email=email,
        first_name=body.first_name.strip(),
        last_name=body.last_name.strip(),
        phone_number=phone,
        hashed_password=hash_password(body.password),
        balance_cents=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if phone is not None:
        background_tasks.add_task(
            _send_signup_welcome,
            phone,
            user.first_name,
            user.last_name,
            user.email,
        )

    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    identifier = body.identifier.strip()

    if "@" in identifier:
        user = _user_by_email(db, identifier.lower())
    else:
        user = _user_by_phone(db, identifier)

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=AuthenticatedUserResponse)
def me(user: CurrentUser) -> AuthenticatedUserResponse:
    return _to_user_response(user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    body: DeleteAccountRequest, user: CurrentUser, db: DbSession
) -> None:
    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if not verify_password(body.password, row.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    open_lock = db.scalar(
        select(Lock.id).where(
            Lock.user_id == row.id, Lock.status.in_(("open", "pending_verification"))
        )
    )

    if open_lock is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Close or settle open FareLocks before deleting your account",
        )

    if row.balance_cents > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Withdraw your wallet balance before deleting your account",
        )

    lock_ids = list(db.scalars(select(Lock.id).where(Lock.user_id == row.id)))
    if lock_ids:
        db.execute(delete(LedgerEntry).where(LedgerEntry.lock_id.in_(lock_ids)))
    db.execute(delete(LedgerEntry).where(LedgerEntry.user_id == row.id))
    db.execute(delete(Lock).where(Lock.user_id == row.id))
    db.delete(row)
    db.commit()
