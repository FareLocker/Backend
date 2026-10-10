import pytest
from fastapi.testclient import TestClient
import jwt

from app import auth
from app.auth import AuthenticatedUser, get_current_user
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_me_returns_identity_for_a_verified_user(client):
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="3e54e3eb-18f5-4bca-aabf-87197acd33f0",
        email="traveler@example.com",
        claims={"role": "authenticated"},
    )
    try:
        response = client.get("/auth/me", headers={"Authorization": "Bearer valid-token"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json() == {
        "id": "3e54e3eb-18f5-4bca-aabf-87197acd33f0",
        "email": "traveler@example.com",
    }


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}])
def test_me_rejects_missing_or_malformed_bearer_tokens(client, headers):
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_verifier_requires_asymmetric_algorithms_and_authenticated_role(monkeypatch):
    class SigningKey:
        key = object()

    class JwksClient:
        def get_signing_key_from_jwt(self, _token):
            return SigningKey()

    decoded_claims = {
        "sub": "3e54e3eb-18f5-4bca-aabf-87197acd33f0",
        "email": "traveler@example.com",
        "role": "authenticated",
    }
    decode_kwargs = {}

    def decode(*_args, **kwargs):
        decode_kwargs.update(kwargs)
        return decoded_claims

    monkeypatch.setattr(auth.settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: JwksClient())
    monkeypatch.setattr(auth.jwt, "decode", decode)

    user = auth.verify_access_token("valid-token")
    assert user.id == decoded_claims["sub"]
    assert user.email == decoded_claims["email"]
    assert decode_kwargs["algorithms"] == ("ES256", "RS256")

    decoded_claims["role"] = "anon"
    with pytest.raises(ValueError, match="not an authenticated"):
        auth.verify_access_token("anon-token")


def test_verifier_rejects_expired_tokens(monkeypatch):
    class JwksClient:
        def get_signing_key_from_jwt(self, _token):
            raise jwt.ExpiredSignatureError("expired")

    monkeypatch.setattr(auth.settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: JwksClient())

    with pytest.raises(ValueError, match="Invalid Supabase access token"):
        auth.verify_access_token("expired-token")
