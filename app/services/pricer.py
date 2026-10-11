from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session
import numpy as np

from app.services.curves import make_f_flight
from app.services.vol import get_sigma, load_latest_curve
from app.config import settings

PAYOUT_CAP = 0.5 # max payout = PAYOUT_CAP * K

def american_put_crr(
        X0: float, K: float,
        f_schedule: Sequence[float],
        sigma: float,
        n_steps: int,
    *,
        payout_cap_frac: float = PAYOUT_CAP,
        dt: float = 1.0
) -> float:
    """CRR American put on detrended fare X, valued in dollars.

    Lattice evolves X; at step i node j the dollar fare is
        S = X0 * u^j * d^(i-j) * f_schedule[i]
    Intrinsic = min(max(K - S, 0), payout_cap_frac * K)
    Early exercise at every node. r = 0 (no discounting)

    Args:
        X0: Spot residual X = S0 / f_flight at purchase.
        K: Strike (= purchase fare).
        f_schedule: Length n_steps +1. f_schedule[i] is the
            flight curve level (dollars) at lattice step i
            (days-to-departure d0 - i).
        sigma: Per-step vol of X (use daily sigma with dt=1)
        n_steps: Number of CRR steps (typically T_days / d0).
        payout_cap_frac: Cap on put payoff as a fraction of K.
        dt: Step size in the same units as sigma (default 1 day).

    Returns:
        Model value V in dollars.
    """
    if n_steps < 0:
        raise ValueError("n_steps must be >= 0")
    if len(f_schedule) != n_steps + 1:
        raise ValueError("f_schedule must have length n_steps + 1")
    if X0 <= 0 or K <= 0:
        raise ValueError("X0 and K must be positive")
    if sigma < 0:
        raise ValueError("sigma must be >= 0")

    cap = payout_cap_frac * K

    if n_steps == 0:
        S = X0 * float(f_schedule[0])
        return float(min(max(K - S, 0.0), cap))

    if sigma == 0.0:
        # Single path: X fixed, only f_schedule moves. Avoids u=d=1 -> 0/0
        V = min(max(K - X0 * float(f_schedule[n_steps]), 0.0), cap)
        for i in range(n_steps - 1, -1, -1):
            intrinsic = min(max(K - X0 * float(f_schedule[i]), 0.0), cap)
            V = max(intrinsic, V)
        return float(V)


    u = float(np.exp(sigma * np.sqrt(dt)))
    d = 1.0 / u
    p = (1.0 - d) / (u - d)
    q = 1.0 - p

    V = np.empty(n_steps + 1, dtype=float)
    for j in range(n_steps + 1):
        X = X0 * (u**j) * (d ** (n_steps-j))
        S = X * float(f_schedule[n_steps])
        V[j] = min(max(K - S, 0.0), cap)

    for i in range(n_steps - 1, -1, -1):
        f_i = float(f_schedule[i])
        V_next = np.empty(i + 1, dtype=float)
        for j in range(i + 1):
            cont = p * V[j + 1] + q * V[j]
            X = X0 * (u**j) * (d ** (i - j))
            S = X * f_i
            intrinsic = min(max(K - S, 0.0), cap)
            V_next[j] = max(intrinsic, cont)
        V = V_next

    return float(V[0])

@dataclass
class Quote:
    V_model: float
    fee: float
    lam: float
    sigma_used: float
    K: float
    T_days: int
    d0: int
    X0: float

def quote_lock(
    *,
    route: tuple[str, str],
    flight_no: str | None,
    _departure_date: date,
    S0: float,  # current market fare (spot)
    d0: int,  # days to departure at purchase
    dow: int,
    hol: int = 0,
    K: float | None = None,  # strike; defaults to S0 (ATM)
    db: Session,
) -> Quote:
    """Price a FareLock: CRR American put + (1 + lambda) loading.

    Loads the latest curve fit for the route, builds f_flight,
    sets X0 = S0 / f_flight(d0, ...), uses K (defaulting to S0),
    pulls sigma for the current d-bucket, runs american_put_crr
    over T = d0 daily steps, and applies base_lambda inventory loading.

    departure_date is accepted for the quote API / Lock row; the
    lattice uses d0/dow/hol (dow frozen at purchase).
    """
    if S0 <= 0:
        raise ValueError("S0 must be positive")
    if K is None:
        K = S0
    if K <= 0:
        raise ValueError("K must be positive")
    if d0 < 0:
        raise ValueError("d0 must be >= 0")

    curve = load_latest_curve(route, db)
    if curve is None:
        raise ValueError(f"no curve fit for {route[0]}-{route[1]}")

    f_flight = make_f_flight(curve.params, curve.flight_premia or {})

    # Freeze dow/hol at purchase; only d declines along the path.
    f_schedule = [
        f_flight(max(d0 - i, 1), dow, hol, flight_no) for i in range(d0 + 1)
    ]
    f0 = f_schedule[0]
    if f0 <= 0:
        raise ValueError("f_flight returned non-positive level")

    X0 = S0 / f0
    T_days = d0
    # Average bucket sigma along the remaining d path (still one CRR sigma).
    sigmas = [get_sigma(route, max(d0 - i, 1), db) for i in range(T_days + 1)]
    sigma = float(sum(sigmas) / len(sigmas))
    lam = float(settings.base_lambda)

    V = american_put_crr(
        X0=X0,
        K=K,
        f_schedule=f_schedule,
        sigma=sigma,
        n_steps=T_days,
    )
    fee = (1.0 + lam) * V

    return Quote(
        V_model=V,
        fee=fee,
        lam=lam,
        sigma_used=sigma,
        K=K,
        T_days=T_days,
        d0=d0,
        X0=X0
    )



