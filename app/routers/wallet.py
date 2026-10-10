"""Authenticated wallet endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import LedgerEntry, User
from app.schemas import (
    LedgerEntryResponse,
    WalletAddFundsRequest,
    WalletAddFundsResponse,
    WalletResponse,
    WalletTransactionResponse,
    WalletWithdrawResponse,
)

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
    "/{user_id}/wallet/withdraw",
    response_model=WalletWithdrawResponse,
    status_code=status.HTTP_200_OK,
)
def withdraw_wallet_funds(
    user_id: int, request: WalletAddFundsRequest, user: CurrentUser, db: DbSession
) -> WalletWithdrawResponse:
    _require_self(user_id, user)

    row = db.get(User, user.id)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if row.balance_cents < request.amount_cents:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Insufficient funds",
        )

    entry = LedgerEntry(
        user_id=row.id,
        amount_cents=-request.amount_cents,
        entry_type="withdrawal",
        note=request.note,
        lock_id=None,
    )

    row.balance_cents -= request.amount_cents
    db.add(entry)
    db.commit()
    db.refresh(row)
    db.refresh(entry)

    return WalletWithdrawResponse(
        user_id=row.id,
        balance_cents=row.balance_cents,
        debited_cents=request.amount_cents,
        ledger_entry_id=entry.id,
    )


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


EntryType = Literal["recharge", "lock_fee", "payout", "withdrawal"]


@router.get("/{user_id}/wallet/transactions", response_model=WalletTransactionResponse)
def get_wallet_transactions(
    user_id: int,
    user: CurrentUser,
    db: DbSession,
    entry_type: EntryType | None = Query(default=None),
) -> WalletTransactionResponse:
    _require_self(user_id, user)

    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user.id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
    )

    if entry_type is not None:
        stmt = stmt.where(LedgerEntry.entry_type == entry_type)

    rows = db.scalars(stmt).all()

    return WalletTransactionResponse(
        user_id=user.id,
        transactions=[
            LedgerEntryResponse(
                id=row.id,
                amount_cents=row.amount_cents,
                entry_type=row.entry_type,
                note=row.note,
                lock_id=row.lock_id,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )
