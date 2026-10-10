from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.services.pricer import PAYOUT_CAP, american_put_crr, quote_lock


def _flat_schedule(level: float, n_steps: int) -> list[float]:
    return [level] * (n_steps + 1)
def test_n_steps_zero_atm_is_zero():
    # X0=1, f=K → S=K → put worth 0
    K = 200.0
    V = american_put_crr(X0=1.0, K=K, f_schedule=[K], sigma=0.2, n_steps=0)
    assert V == pytest.approx(0.0)
def test_n_steps_zero_itm_capped():
    K = 200.0
    # S = 1 * 50 = 50 → raw put 150, cap = 0.5*200 = 100
    V = american_put_crr(X0=1.0, K=K, f_schedule=[50.0], sigma=0.2, n_steps=0)
    assert V == pytest.approx(PAYOUT_CAP * K)
def test_sigma_zero_flat_curve_atm():
    K = 100.0
    V = american_put_crr(
        X0=1.0, K=K, f_schedule=_flat_schedule(K, 5), sigma=0.0, n_steps=5
    )
    assert V == pytest.approx(0.0)
def test_sigma_zero_declining_curve_american():
    """f drops over time → later intrinsic rises; American takes the max (capped)."""
    K = 100.0
    # S_i = 1 * f_i; raw end intrinsic = 60, but cap = 0.5*K = 50
    f_schedule = [100.0, 90.0, 70.0, 40.0]
    V = american_put_crr(X0=1.0, K=K, f_schedule=f_schedule, sigma=0.0, n_steps=3)
    assert V == pytest.approx(PAYOUT_CAP * K)
def test_higher_sigma_raises_value():
    K = 200.0
    sched = _flat_schedule(200.0, 10)
    v_lo = american_put_crr(X0=1.0, K=K, f_schedule=sched, sigma=0.05, n_steps=10)
    v_hi = american_put_crr(X0=1.0, K=K, f_schedule=sched, sigma=0.20, n_steps=10)
    assert v_hi > v_lo > 0
def test_value_nonnegative_and_capped():
    K = 300.0
    V = american_put_crr(
        X0=1.0,
        K=K,
        f_schedule=_flat_schedule(300.0, 15),
        sigma=0.5,
        n_steps=15,
    )
    assert V >= 0.0
    assert V <= PAYOUT_CAP * K + 1e-9
def test_otm_rich_spot_small_value():
    # Fare rich vs curve: X0=1.3, flat f=K/1.3 so S0=K; still some put value from vol
    K = 200.0
    f = K / 1.3
    V = american_put_crr(
        X0=1.3, K=K, f_schedule=_flat_schedule(f, 8), sigma=0.05, n_steps=8
    )
    assert V >= 0.0
    assert V < american_put_crr(
        X0=1.0, K=K, f_schedule=_flat_schedule(K, 8), sigma=0.05, n_steps=8
    )
def test_rejects_bad_inputs():
    with pytest.raises(ValueError):
        american_put_crr(X0=1.0, K=100.0, f_schedule=[100.0], sigma=0.1, n_steps=-1)
    with pytest.raises(ValueError):
        american_put_crr(X0=1.0, K=100.0, f_schedule=[100.0, 100.0], sigma=0.1, n_steps=0)
    with pytest.raises(ValueError):
        american_put_crr(X0=-1.0, K=100.0, f_schedule=[100.0], sigma=0.1, n_steps=0)
    with pytest.raises(ValueError):
        american_put_crr(X0=1.0, K=100.0, f_schedule=[100.0, 100.0], sigma=-0.1, n_steps=1)
def test_quote_lock_wires_fee_and_loading():
    params = {
        "intercept": 0.0,  # unused — we mock make_f_flight path via curve + patch
        "holiday": 0.0,
    }
    # Minimal keys make_f needs if not patched — better patch make_f_flight
    curve = SimpleNamespace(params=params, flight_premia={})
    def fake_f_flight(d, dow, hol, flight_no):
        return 200.0
    db = MagicMock()
    with (
        patch("app.services.pricer.load_latest_curve", return_value=curve),
        patch("app.services.pricer.make_f_flight", return_value=fake_f_flight),
        patch("app.services.pricer.get_sigma", return_value=0.10),
    ):
        q = quote_lock(
            route=("ATL", "JFK"),
            flight_no="DL 1",
            _departure_date=date(2026, 10, 20),
            S0=200.0,
            d0=5,
            dow=2,
            db=db,
        )
    assert q.K == 200.0
    assert q.d0 == 5
    assert q.T_days == 5
    assert q.X0 == pytest.approx(1.0)
    assert q.sigma_used == pytest.approx(0.10)
    assert q.lam == pytest.approx(settings.base_lambda)
    assert q.fee == pytest.approx((1.0 + settings.base_lambda) * q.V_model)
    assert q.V_model > 0.0
