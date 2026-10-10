"""Authenticated wallet endpoints. Fund settlement is intentionally a stub."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.schemas import WalletAddFundsRequest, WalletAddFundsResponse, WalletResponse

router = APIRouter(prefix="/users", tags=["wallet"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{user_id}/wallet", response_model=WalletResponse)
def get_wallet(user_id: int, user: CurrentUser, db: DbSession) -> WalletResponse:
    if user_id != user.id:
        raise HTTPException(
            status_code=403, detail="Cannot access another user's wallet"
        )
    return WalletResponse(user_id=user.id, balance_cents=user.balance_cents)


@router.post(
    "/{user_id}/wallet/add", response_model=WalletAddFundsResponse, status_code=501
)
def add_wallet_funds(
    user_id: int,
    request: WalletAddFundsRequest,
    user: CurrentUser,
) -> WalletAddFundsResponse:
    if user_id != user.id:
        raise HTTPException(
            status_code=403, detail="Cannot modify another user's wallet"
        )
    return WalletAddFundsResponse(
        message=f"Top-up of {request.amount_cents} cents is not implemented yet."
    )
