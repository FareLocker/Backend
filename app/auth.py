"""Supabase access-token verification for protected API routes."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


SUPPORTED_ALGORITHMS = ("ES256", "RS256")
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None
    claims: dict[str, Any]


class AuthConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600)


def verify_access_token(token: str) -> AuthenticatedUser:
    """Verify a Supabase asymmetric JWT and return its authenticated identity."""
    issuer = settings.supabase_issuer
    jwks_url = settings.supabase_jwks_url
    if issuer is None or jwks_url is None:
        raise AuthConfigurationError("Supabase authentication is not configured")

    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=SUPPORTED_ALGORITHMS,
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.PyJWTError as error:
        raise ValueError("Invalid Supabase access token") from error

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("Supabase access token is missing a subject")
    if claims.get("role") != "authenticated":
        raise ValueError("Supabase access token is not an authenticated user token")

    email = claims.get("email")
    return AuthenticatedUser(
        id=user_id,
        email=email if isinstance(email, str) else None,
        claims=claims,
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedUser:
    if credentials is None:
        raise _unauthorized()
    try:
        return verify_access_token(credentials.credentials)
    except AuthConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        ) from error
    except ValueError as error:
        raise _unauthorized() from error


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
