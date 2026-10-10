from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.services.curves import D_BUCKETS
from app.services.vol import (
    PRIOR_STRENGTH,
    build_x,
    fit_bucket_vols,
    log_returns_by_series,
)


def _flat_f_flight(level: float = 100.0):
    def f_flight(d, dow, hol, flight_no):
        return level

    return f_flight


def test_build_x_drops_non_positive():
    obs = pd.DataFrame(
        {
            "fare": [100.0, 0.0, -5.0],
            "d": [10, 10, 10],
            "dow": [2, 2, 2],
            "holiday": [0, 0, 0],
            "flight_no": ["DL1", "DL1", "DL1"],
            "departure_date": [pd.Timestamp("2026-06-01")] * 3,
            "queried_at": [datetime(2026, 1, 1, tzinfo=timezone.utc)] * 3,
        }
    )
    xdf = build_x(obs, _flat_f_flight(100.0))
    assert len(xdf) == 1
    assert xdf.iloc[0]["X"] == pytest.approx(1.0)


def test_log_returns_by_series_tags_ending_d():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame(
        {
            "flight_no": ["DL1", "DL1", "DL1"],
            "departure_date": [pd.Timestamp("2026-06-01")] * 3,
            "queried_at": [t0, t0 + timedelta(days=1), t0 + timedelta(days=2)],
            "X": [1.0, np.e, np.e**2],  # log returns: 1, 1
            "d": [20, 19, 18],
        }
    )
    rets = log_returns_by_series(df)
    assert len(rets) == 2
    assert rets["ret"].tolist() == pytest.approx([1.0, 1.0])
    assert rets["d"].tolist() == [19, 18]


def test_log_returns_separates_series():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame(
        {
            "flight_no": ["DL1", "DL1", "DL2", "DL2"],
            "departure_date": [pd.Timestamp("2026-06-01")] * 4,
            "queried_at": [
                t0,
                t0 + timedelta(days=1),
                t0,
                t0 + timedelta(days=1),
            ],
            "X": [1.0, 2.0, 1.0, 4.0],
            "d": [10, 9, 10, 9],
        }
    )
    rets = log_returns_by_series(df)
    assert len(rets) == 2
    assert sorted(rets["ret"].tolist()) == pytest.approx(
        sorted([np.log(2.0), np.log(4.0)])
    )


def test_fit_bucket_vols_empty_uses_prior():
    rets = pd.DataFrame(columns=["ret", "d"])
    fits = fit_bucket_vols(rets)
    assert len(fits) == len(D_BUCKETS)
    prior = float(settings.vol_prior)
    for fit, (lo, hi) in zip(fits, D_BUCKETS):
        assert fit["bucket_lo"] == lo
        assert fit["bucket_hi"] == hi
        assert fit["n_obs"] == 0
        assert fit["shrink_w"] == 0.0
        assert fit["sigma"] == pytest.approx(prior)


def test_fit_bucket_vols_shrinkage_formula():
    # 5 identical-bucket returns in d=10 → bucket (8,14)
    sample = np.array([0.01, -0.02, 0.03, -0.01, 0.00])
    rets = pd.DataFrame({"ret": sample, "d": [10] * len(sample)})
    fits = fit_bucket_vols(rets)
    target = next(f for f in fits if f["bucket_lo"] == 8)
    n = len(sample)
    w = n / (n + PRIOR_STRENGTH)
    sigma_hat = float(np.std(sample, ddof=1))
    expected = w * sigma_hat + (1 - w) * float(settings.vol_prior)
    assert target["n_obs"] == n
    assert target["shrink_w"] == pytest.approx(w)
    assert target["sigma"] == pytest.approx(expected)
    # other buckets stay at prior
    prior = float(settings.vol_prior)
    for f in fits:
        if f["bucket_lo"] != 8:
            assert f["sigma"] == pytest.approx(prior)


def test_build_x_detrends_to_one_when_on_curve():
    obs = pd.DataFrame(
        {
            "fare": [100.0, 100.0],
            "d": [10, 10],
            "dow": [2, 2],
            "holiday": [0, 0],
            "flight_no": ["DL1", "DL1"],
            "departure_date": [pd.Timestamp("2026-06-01")] * 2,
            "queried_at": [
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            ],
        }
    )
    xdf = build_x(obs, _flat_f_flight(100.0))
    assert np.allclose(xdf["X"], 1.0)
