from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, create_access_token, hash_password, verify_password
from app.db import get_db
from app.models import User
from app.schemas import (
    AuthenticatedUserResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_db)]


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
def register(body: RegisterRequest, db: DbSession) -> TokenResponse:
    email = body.email.lower().strip()
    if _user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    phone = body.phone_number.strip() if body.phone_number else None  # type: ignore[union-attr]

    if phone is not None and _user_by_phone(db, phone) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number already registered",
        )

    user = User(
        email=email,
        first_name=body.first_name.strip(),
        last_name=body.last_name.strip(),
        phone_number=(body.phone_number.strip() if body.phone_number else None),
        hashed_password=hash_password(body.password),
        balance_cents=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

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
