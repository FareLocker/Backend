from datetime import datetime, timezone
from typing import Callable
import numpy as np
import pandas as pd
from app.models import CurveFit, FareObservation
from sqlalchemy.orm import Session

from app.constants import ROUTES

D_BUCKETS = [(1, 3), (4, 7), (8, 14), (15, 21), (22, 30), (31, 45), (46, 60)]
WED = 2  # Wednesday baseline for day-of-week dummies


def load_observations(route: tuple[str, str], db: Session) -> pd.DataFrame:
    """Load all fare observations for one route.

    Args:
        route: Directional route string, e.g. ('ATL', 'JFK').
        db: SQLAlchemy session.

    Returns:
        DataFrame with columns fare, d, dow, holiday, flight_no,
        departure_date — the fitter input contract. dow and holiday
        are derived from departure_date here, not stored.
    """

    rows = (
        db.query(FareObservation)
        .filter(FareObservation.origin == route[0], FareObservation.dest == route[1])
        .all()
    )
    df = pd.DataFrame(
        [
            {
                "fare": r.price,
                "d": r.d,
                "departure_date": r.departure_date,
                "flight_no": r.flight_no,
            }
            for r in rows
        ]
    )
    if not rows:
        return pd.DataFrame(
            columns=["fare", "d", "dow", "holiday", "flight_no", "departure_date"]
        )
    df["dow"] = pd.to_datetime(df["departure_date"]).dt.weekday
    df["holiday"] = 0
    return df


def save_curve_fit(route: tuple[str, str], params: dict, db: Session) -> None:
    """Persist a fitted term-structure param dict.

    Args:
        route: Directional route tuple, e.g. ('ATL','JFK').
        params: Flat coefficient dict as returned by fit_route_curve.
        db: SQLAlchemy session.

    Appends a new row to curve_fits (with empty flight_premia);
    readers always take the latest row per route. Append-only —
    no upsert logic, full fit history retained.
    """

    db.add(
        CurveFit(
            route=f"{route[0]}-{route[1]}",
            fitted_at=datetime.now(timezone.utc),
            params=params,
            flight_premia={},
        )
    )
    db.commit()


def fit_route_curve(df: pd.DataFrame) -> dict:
    """OLS fit of the route-level term structure.

    Model: ln S_i = α + s(d_i) + β_{dow(i)} + γ·holiday(i) + ε_i,
    where s(d) is a step function over D_BUCKETS with the [1,3]
    bucket absorbed into the intercept.

    Args:
        df: DataFrame with columns log_fare (float, natural log of the
            cheapest observed fare), d (int, days to departure),
            dow (int, 0=Monday..6=Sunday), holiday (int, 0/1).
            One row per departure_date (cheapest-fare index).

    Returns:
        Flat dict of OLS coefficients: "intercept", six d-bucket
        dummies ("d_4_7" … "d_46_60"), six weekday dummies
        ("dow_0", "dow_1", "dow_3", "dow_4", "dow_5", "dow_6";
        Wednesday is the baseline), and "holiday".
    """

    n = len(df)
    X = np.ones((n, 1))
    names = ["intercept"]
    for lo, hi in D_BUCKETS[1:]:
        X = np.c_[X, ((df["d"] >= lo) & (df["d"] <= hi)).to_numpy(float)]
        names.append(f"d_{lo}_{hi}")
    for dw in [0, 1, 3, 4, 5, 6]:  # Wednesday baseline
        X = np.c_[X, (df["dow"] == dw).to_numpy(float)]
        names.append(f"dow_{dw}")
    X = np.c_[X, df["holiday"].to_numpy(float)]
    names.append("holiday")
    beta, *_ = np.linalg.lstsq(X, df["log_fare"].to_numpy(), rcond=None)
    return dict(zip(names, beta))


def make_f(params: dict) -> Callable[[int, int, int], float]:
    """Build the route fare curve f(d, dow, hol) from fitted params.

    Args:
        params: Flat coefficient dict as returned by fit_route_curve:
            "intercept", d-bucket dummies ("d_4_7" … "d_46_60"),
            weekday dummies ("dow_0", "dow_1", "dow_3", "dow_4",
            "dow_5", "dow_6"; Wednesday baseline), "holiday".

    Returns:
        Callable f(d, dow, hol) -> float: the conditional geometric
        mean fare in dollars for a departure d days out on weekday
        dow (0=Monday..6=Sunday) with holiday flag hol (0/1).
        d is clamped to [1, 60] — the curve never extrapolates
        past observed data.
    """

    b = params

    def f(d: int, dow: int, hol: int) -> float:
        dc = min(max(int(d), 1), 60)
        s = next((b[f"d_{lo}_{hi}"] for lo, hi in D_BUCKETS[1:] if lo <= dc <= hi), 0.0)
        bd = 0.0 if dow == WED else b[f"dow_{dow}"]
        return float(np.exp(b["intercept"] + s + bd + b["holiday"] * hol))

    return f


def refit_all_routes() -> None:
    """Refit the term structure for every route and persist the params.

    For each route: load fare observations, build the cheapest-fare
    index (one row per departure_date), fit via fit_route_curve,
    and upsert the resulting param dict into the curve_fits table.

    Routes with fewer than 30 index rows are skipped — the previous
    fit stands until enough data accumulates. Intended to run hourly
    from the scheduler (see app/jobs.py).
    """
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        for route in ROUTES:
            obs = load_observations(route, db)
            if len(obs) < 30:
                continue  # keep previous fit
            idx = obs.loc[obs.groupby("departure_date")["fare"].idxmin()].copy()
            idx["log_fare"] = np.log(idx["fare"])
            params = fit_route_curve(idx)
            save_curve_fit(route, params, db)
    finally:
        db.close()
