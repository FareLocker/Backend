"""Authenticated wallet endpoints. Fund settlement is intentionally a stub."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Wallet
from app.schemas import WalletAddFundsRequest, WalletAddFundsResponse, WalletResponse

router = APIRouter(prefix="/users", tags=["wallet"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{user_id}/wallet", response_model=WalletResponse)
def get_wallet(user_id: str, user: CurrentUser, db: DbSession) -> WalletResponse:
    """Return the signed-in user's wallet, creating an empty wallet if needed."""
    # Never allow a caller to read another user's wallet by changing the path.
    if user_id != user.id:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Cannot access another user's wallet")

    wallet = db.get(Wallet, user.id)
    if wallet is None:
        wallet = Wallet(user_id=user.id, balance_cents=0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return WalletResponse(user_id=wallet.user_id, balance_cents=wallet.balance_cents)


@router.post("/{user_id}/wallet/add", response_model=WalletAddFundsResponse, status_code=501)
def add_wallet_funds(
    user_id: str,
    request: WalletAddFundsRequest,
    user: CurrentUser,
) -> WalletAddFundsResponse:
    """Declare the top-up contract; payment processing is not implemented."""
    if user_id != user.id:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Cannot modify another user's wallet")
    # Do not credit funds until a payment provider confirms the transaction.
    return WalletAddFundsResponse(
        message=f"Top-up of {request.amount_cents} cents is not implemented yet."
    )
