"""Authenticated wallet endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import LedgerEntry, User
from app.schemas import WalletAddFundsRequest, WalletAddFundsResponse, WalletResponse

router = APIRouter(prefix="/users", tags=["wallet"])
DbSession = Annotated[Session, Depends(get_db)]


def _require_self(user_id: int, user: User) -> None:
    if user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's wallet",
        )


@router.get("/{user_id}/wallet", response_model=WalletResponse)
def get_wallet(user_id: int, user: CurrentUser, db: DbSession) -> WalletResponse:
    _require_self(user_id, user)
    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return WalletResponse(user_id=row.id, balance_cents=row.balance_cents)


@router.post(
    "/{user_id}/wallet/add",
    response_model=WalletAddFundsResponse,
    status_code=status.HTTP_200_OK,
)
def add_wallet_funds(
    user_id: int,
    request: WalletAddFundsRequest,
    user: CurrentUser,
    db: DbSession,
) -> WalletAddFundsResponse:
    _require_self(user_id, user)

    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    entry = LedgerEntry(
        user_id=row.id,
        amount_cents=request.amount_cents,
        entry_type="recharge",
        note=request.note,
        lock_id=None,
    )

    row.balance_cents += request.amount_cents
    db.add(entry)
    db.commit()
    db.refresh(row)
    db.refresh(entry)

    return WalletAddFundsResponse(
        user_id=row.id,
        balance_cents=row.balance_cents,
        credited_cents=request.amount_cents,
        ledger_entry_id=entry.id,
    )
