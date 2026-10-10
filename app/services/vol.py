import pandas as pd
from datetime import datetime, timezone
import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import ROUTES

from app.models import CurveFit, VolFit
from app.services.curves import D_BUCKETS, load_observations, make_f_flight

# Pseudo-observations for prior strength in shrinkage:
# sigma_bar = w * sigma_hat + (1 - w) * sigma_prior
# w = n / (n + PRIOR_STRENGTH)
PRIOR_STRENGTH = 20


def load_latest_curve(route: tuple[str, str], db: Session) -> CurveFit | None:
    """Latest CurveFit row for a route, or None if never fitted."""
    key = f"{route[0]}-{route[1]}"
    return (
        db.query(CurveFit)
        .filter(CurveFit.route == key)
        .order_by(CurveFit.fitted_at.desc())
        .first()
    )


def build_x(obs: pd.DataFrame, f_flight) -> pd.DataFrame:
    """Detrend fares: X = S / f_flight.

    Args:
        obs: Route observations with fare, d, dow, holiday, flight_no,
            departure_date, queried_at.
        f_flight: Callable from make_f_flight.

    Returns:
        Copy of obs with column X (positive floats). Row with non-positive
        fare or non-positive f_flight are dropped.
    """
    df = obs.dropna(subset=["fare"]).copy()
    if df.empty:
        return df

    levels = [
        f_flight(int(d), int(dow), int(hol), fn if pd.notna(fn) else None)
        for d, dow, hol, fn in zip(df["d"], df["dow"], df["holiday"], df["flight_no"])
    ]
    df["f_flight"] = levels
    df = df[(df["fare"] > 0) & (df["f_flight"] > 0)].copy()
    df["X"] = df["fare"].to_numpy(float) / df["f_flight"].to_numpy(float)
    return df


def log_returns_by_series(df: pd.DataFrame) -> pd.DataFrame:
    """Log returns of X along each (flight_no, departure_date) path.

    Series are sorted by queried_at. Each return is tagged with the ending
    observation's d (the d after the move) for bucket assignment.

    Returns:
        DataFrame with columns ret, d - one row per consecutive pair.
    """
    rows = df.dropna(subset=["flight_no", "X", "queried_at"]).copy()
    if rows.empty:
        return pd.DataFrame(columns=["ret", "d"])

    out = []
    for _, grp in rows.groupby(["flight_no", "departure_date"]):
        g = grp.sort_values("queried_at")
        if len(g) < 2:
            continue
        x = g["X"].to_numpy(float)
        d = g["d"].to_numpy(int)
        rets = np.diff(np.log(x))
        # tag return with ending d
        for r, d_end in zip(rets, d[1:]):
            if np.isfinite(r):
                out.append({"ret": float(r), "d": int(d_end)})
    return pd.DataFrame(out) if out else pd.DataFrame(columns=["ret", "d"])


def fit_bucket_vols(rets: pd.DataFrame) -> list[dict]:
    """Sample sigma per d-bucket, shrink toward settings.vol_prior.

    Args:
        rets: DataFrame with columns ret, d from log_returns_by_series.

    Returns:
        List of dicts: bucket_lo, bucket_hi, sigma, n_obs, shrink_w -
        one entry per D_BUCKETS entry. Empty buckets get sigma = prior, w = 0.
    """
    prior = float(settings.vol_prior)
    fits = []
    for lo, hi in D_BUCKETS:
        sample = rets[(rets["d"] >= lo) & (rets["d"] <= hi)]["ret"].to_numpy(float)
        n = int(len(sample))
        if n >= 2:
            sigma_hat = float(np.std(sample, ddof=1))
            w = n / (n + PRIOR_STRENGTH)
            sigma = w * sigma_hat + (1.0 - w) * prior
        else:
            w = 0.0
            sigma = prior
        fits.append(
            {
                "bucket_lo": lo,
                "bucket_hi": hi,
                "sigma": float(sigma),
                "n_obs": n,
                "shrink_w": float(w),
            }
        )
    return fits


def save_vol_fits(route: tuple[str, str], fits: list[dict], db: Session) -> None:
    """Append one VolFit row per bucket (append-only history)."""
    now = datetime.now(timezone.utc)
    key = f"{route[0]}-{route[1]}"
    for fit in fits:
        db.add(
            VolFit(
                route=key,
                fitted_at=now,
                bucket_lo=fit["bucket_lo"],
                bucket_hi=fit["bucket_hi"],
                sigma=fit["sigma"],
                n_obs=fit["n_obs"],
                shrink_w=fit["shrink_w"],
            )
        )
    db.commit()


def get_sigma(route: tuple[str, str] | str, d: int, db: Session) -> float:
    """Latest shrunk sigma for the d-bucket covering d; falls back to prior."""
    key = route if isinstance(route, str) else f"{route[0]}-{route[1]}"
    dc = min(max(int(d), 1), 60)
    row = (
        db.query(VolFit)
        .filter(VolFit.route == key, VolFit.bucket_lo <= dc, VolFit.bucket_hi >= dc)
        .order_by(VolFit.fitted_at.desc())
        .first()
    )
    if row is None:
        return float(settings.vol_prior)
    return float(row.sigma)


def fit_route_vol(route: tuple[str, str], db: Session) -> list[dict] | None:
    """End-to-end vol fit for one route. Returns fit dicts, or None if no curve."""
    curve = load_latest_curve(route, db)
    if curve is None:
        return None

    obs = load_observations(route, db)
    if obs.empty:
        return None

    f_flight = make_f_flight(curve.params, curve.flight_premia or {})
    xdf = build_x(obs, f_flight)
    rets = log_returns_by_series(xdf)
    return fit_bucket_vols(rets)


def refit_all_vols() -> None:
    """Refit vol for every route that has a curve fit. Append-only."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        for route in ROUTES:
            fits = fit_route_vol(route, db)
            if fits is None:
                continue
            save_vol_fits(route, fits, db)
    finally:
        db.close()
