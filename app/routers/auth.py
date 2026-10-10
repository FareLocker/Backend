from fastapi import APIRouter

from app.auth import CurrentUser
from app.schemas import AuthenticatedUserResponse


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthenticatedUserResponse)
def me(user: CurrentUser) -> AuthenticatedUserResponse:
    return AuthenticatedUserResponse(id=user.id, email=user.email)
