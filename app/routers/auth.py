import json
import logging
import re
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, Header, HTTPException, status
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
    MessagingSetupResponse,
    RegisterRequest,
    RegistrationResponse,
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


def _provision_signup_messaging(
    phone_number: str, first_name: str, last_name: str, email: str
) -> dict[str, str] | None:
    """Create the project user and return its assigned Spectrum line."""
    if not settings.photon_messaging_url or not settings.photon_messaging_secret:
        logger.warning(
            "Skipping Photon provisioning: worker URL or shared secret is not configured"
        )
        return None

    request = Request(
        f"{settings.photon_messaging_url.rstrip('/')}/provision-user",
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
            payload = json.loads(response.read())
            photon_user_id = payload.get("photon_user_id")
            assigned_phone_number = payload.get("assigned_phone_number")
            if not isinstance(photon_user_id, str) or not isinstance(assigned_phone_number, str):
                raise RuntimeError("Photon returned an invalid provisioning response")
            return {
                "photon_user_id": photon_user_id,
                "assigned_phone_number": assigned_phone_number,
            }
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as error:
        logger.warning("Could not provision Photon signup messaging: %s", error)
        return None


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _user_by_phone(db: Session, phone_number: str) -> User | None:
    return db.scalar(select(User).where(User.phone_number == phone_number))


def _messaging_response(user: User) -> MessagingSetupResponse:
    status_value = user.photon_messaging_status
    if status_value not in {"awaiting_start", "connected"}:
        status_value = "unavailable"
    return MessagingSetupResponse(
        status=status_value,
        assigned_phone_number=user.photon_assigned_phone_number,
    )


def _apply_provisioning(user: User, db: Session) -> MessagingSetupResponse:
    result = _provision_signup_messaging(
        user.phone_number or "", user.first_name, user.last_name, user.email
    )
    if result is None:
        user.photon_messaging_status = "unavailable"
    else:
        user.photon_user_id = result["photon_user_id"]
        user.photon_assigned_phone_number = result["assigned_phone_number"]
        user.photon_messaging_status = "awaiting_start"
    db.commit()
    db.refresh(user)
    return _messaging_response(user)


def _to_user_response(user: User) -> AuthenticatedUserResponse:
    return AuthenticatedUserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        balance_cents=user.balance_cents,
        messaging=_messaging_response(user),
    )


@router.post(
    "/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED
)
def register(
    body: RegisterRequest,
    db: DbSession,
) -> RegistrationResponse:
    email = body.email.lower().strip()
    if _user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    phone = _normalize_phone_number(body.phone_number)
    if phone is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Enter a 10-digit U.S./Canada phone number or an "
                "international number in E.164 format (for example, +15551234567)"
            ),
        )

    if _user_by_phone(db, phone) is not None:
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

    messaging = _apply_provisioning(user, db)
    return RegistrationResponse(
        access_token=create_access_token(user.id), messaging=messaging
    )


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


@router.post("/messaging/retry", response_model=MessagingSetupResponse)
def retry_messaging(user: CurrentUser, db: DbSession) -> MessagingSetupResponse:
    if user.photon_messaging_status in {"awaiting_start", "connected"}:
        return _messaging_response(user)
    return _apply_provisioning(user, db)


def _require_internal_secret(secret: str | None) -> None:
    if not settings.photon_messaging_secret or secret != settings.photon_messaging_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.post("/messaging/start")
def begin_messaging(
    phone_number: str,
    db: DbSession,
    internal_secret: str | None = Header(default=None, alias="X-Photon-Internal-Secret"),
) -> dict[str, str | bool]:
    """Resolve an inbound START without exposing user data publicly."""
    _require_internal_secret(internal_secret)
    user = _user_by_phone(db, phone_number)
    if user is None or user.photon_messaging_status != "awaiting_start":
        return {"should_send_welcome": False}
    return {"should_send_welcome": True, "first_name": user.first_name}


@router.post("/messaging/welcome-delivered", status_code=status.HTTP_204_NO_CONTENT)
def complete_messaging(
    phone_number: str,
    db: DbSession,
    internal_secret: str | None = Header(default=None, alias="X-Photon-Internal-Secret"),
) -> None:
    _require_internal_secret(internal_secret)
    user = _user_by_phone(db, phone_number)
    if user is not None and user.photon_messaging_status == "awaiting_start":
        user.photon_messaging_status = "connected"
        db.commit()


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
