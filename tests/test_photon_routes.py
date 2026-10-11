"""The Photon worker's routes: a texter's locks, wallet and confirmed purchase."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Lock

# The database, client and pricing fixtures are shared with the purchase tests.
from tests.test_lock_purchase import (  # noqa: F401
    _create_user,
    _test_secret,
    client,
    db,
    mock_price,
)

SECRET = "photon-test-secret"
PHONE = "+15551234567"
HEADERS = {"X-Photon-Internal-Secret": SECRET}


@pytest.fixture(autouse=True)
def _photon_secret(monkeypatch):
    monkeypatch.setattr(settings, "photon_messaging_secret", SECRET)


def _linked_user(db, balance_cents: int = 10_000):
    user = _create_user(db, balance_cents=balance_cents)
    user.phone_number = PHONE
    db.commit()
    return user


def _purchase_body() -> dict:
    return {
        "origin": "ATL",
        "destination": "LAX",
        "departure_date": (date.today() + timedelta(days=10)).isoformat(),
        "flight_number": "DL 123",
        "fare_cents": 42000,
    }


def test_wallet_returns_the_linked_accounts_balance(client, db):
    user = _linked_user(db, balance_cents=6250)

    response = client.get(f"/photon/users/{PHONE}/wallet", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"user_id": user.id, "balance_cents": 6250, "currency": "USD"}


def test_purchase_then_list_locks(client, db, mock_price):
    user = _linked_user(db)

    bought = client.post(f"/photon/users/{PHONE}/locks", headers=HEADERS, json=_purchase_body())

    assert bought.status_code == 200
    body = bought.json()
    assert body["flight_number"] == "DL123"
    assert body["balance_cents"] == 8750  # 10,000 minus the $12.50 fee
    lock = db.get(Lock, body["id"])
    assert lock is not None and lock.user_id == user.id

    listed = client.get(f"/photon/users/{PHONE}/locks", headers=HEADERS)
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()["locks"]] == [body["id"]]


def test_purchase_reports_shortfall_without_creating_a_lock(client, db, mock_price):
    _linked_user(db, balance_cents=100)

    response = client.post(f"/photon/users/{PHONE}/locks", headers=HEADERS, json=_purchase_body())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "insufficient_funds"
    assert db.scalars(select(Lock)).all() == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", f"/photon/users/{PHONE}/locks"),
        ("get", f"/photon/users/{PHONE}/wallet"),
        ("post", f"/photon/users/{PHONE}/locks"),
    ],
)
def test_every_route_requires_the_shared_secret(client, db, method, path):
    _linked_user(db)

    kwargs = {"json": _purchase_body()} if method == "post" else {}
    response = getattr(client, method)(
        path, headers={"X-Photon-Internal-Secret": "wrong"}, **kwargs
    )

    assert response.status_code == 401


def test_unlinked_phone_number_is_reported(client, db):
    response = client.get("/photon/users/+15550000000/wallet", headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "phone_not_linked"
