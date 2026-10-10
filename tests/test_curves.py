import numpy as np
import pandas as pd
import pytest

from app.services.curves import (
    D_BUCKETS,
    estimate_flight_premia,
    fit_route_curve,
    make_f,
    make_f_flight,
)


def _synthetic_index(n_per_bucket: int = 8) -> pd.DataFrame:
    """Cheapest-fare index with known structure: higher d -> lower fare, Wed cheaper."""
    rows = []
    day = 0
    for lo, hi in D_BUCKETS:
        d = (lo + hi) // 2
        for i in range(n_per_bucket):
            dow = i % 7
            # baseline ~200 on Wed at d in [1, 3]; step up as d shrinks handled via d level
            log_fare = np.log(200.0) - 0.02 * (d - 2) + (0.0 if dow == 2 else 0.05)
            rows.append(
                {
                    "log_fare": log_fare,
                    "fare": float(np.exp(log_fare)),
                    "d": d,
                    "dow": dow,
                    "holiday": 0,
                    "departure_date": pd.Timestamp("2026-01-01")
                    + pd.Timedelta(days=day),
                }
            )
            day += 1
    return pd.DataFrame(rows)


def test_fit_route_curve_returns_expected_keys():
    df = _synthetic_index()
    params = fit_route_curve(df)
    assert "intercept" in params
    assert "holiday" in params
    for lo, hi in D_BUCKETS[1:]:
        assert f"d_{lo}_{hi}" in params
    for dw in [0, 1, 3, 4, 5, 6]:
        assert f"dow_{dw}" in params
    assert "dow_2" not in params  # Wednesday baseline


def test_make_f_positive_and_clamps_d():
    params = fit_route_curve(_synthetic_index())
    f = make_f(params)
    assert f(10, 2, 0) > 0
    # clamp: d = 0 and d=1 (same bucket) should match; d>60 == d=60
    assert f(0, 2, 0) == f(1, 2, 0)
    assert f(100, 2, 0) == f(60, 2, 0)


def test_make_f_wednesday_is_baseline_when_flat_dow():
    # Force zero DOW/holiday coeffs; only intercept + optional d terms matter
    params = {k: 0.0 for k in fit_route_curve(_synthetic_index())}
    params["intercept"] = np.log(100.0)
    f = make_f(params)
    for dow in range(7):
        assert f(5, dow, 0) == pytest.approx(100.0)


def test_estimate_flight_premia_median_and_min_obs():
    params = {k: 0.0 for k in fit_route_curve(_synthetic_index())}
    params["intercept"] = np.log(100.0)
    f = make_f(params)

    # DL1: always 10% above curve -> delta = ln(1.1)
    # DL2: only 3 obs -> omitted at min_obs=5
    rows = []
    for i in range(6):
        rows.append(
            {"fare": 110.0, "d": 10, "dow": 2, "holiday": 0, "flight_no": "DL1"}
        )
    for i in range(3):
        rows.append(
            {"fare": 150.0, "d": 10, "dow": 2, "holiday": 0, "flight_no": "DL2"}
        )
    obs = pd.DataFrame(rows)
    premia = estimate_flight_premia(obs, f, min_obs=5)
    assert "DL1" in premia
    assert "DL2" not in premia
    assert premia["DL1"] == pytest.approx(np.log(1.1), rel=1e-6)


def test_make_f_flight_applies_delta():
    params = {k: 0.0 for k in fit_route_curve(_synthetic_index())}
    params["intercept"] = np.log(100.0)
    premia = {"DL1": np.log(1.25)}
    ff = make_f_flight(params, premia)
    assert ff(10, 2, 0, None) == pytest.approx(100.0)
    assert ff(10, 2, 0, "UNKNOWN") == pytest.approx(100.0)
    assert ff(10, 2, 0, "DL1") == pytest.approx(125.0)
