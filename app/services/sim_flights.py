"""Demo simulated flights that coexist with live Google results.

Identity is the flight number (carrier ``XD``). Prices follow layered
value-noise (irregular, mean-reverting feel) so mark-to-market / early
exercise can go ITM during a pitch without a obvious sine wave.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import floor
from types import SimpleNamespace

# Primary noise knot spacing. With smooth interpolation, visible drift is
# gradual (~tens of seconds), not a 90s sine loop.
STEP_SECONDS = 180.0
PRICE_FLOOR = 40.0
HISTORY_STEPS = 30
MAX_QUOTE_DAYS = 30
# Kept as an alias so older tests/docs referring to period still resolve.
PERIOD_SECONDS = STEP_SECONDS


@dataclass(frozen=True)
class SimFlightSpec:
    carrier: str
    number: str
    origin: str
    destination: str
    base_price: float
    amplitude: float
    seed: int  # offsets the noise path per flight
    duration_minutes: int
    depart_hour: int
    depart_minute: int
    airline_name: str = "FareLocker Demo Air"

    @property
    def flight_no(self) -> str:
        return f"{self.carrier}{self.number}"


CATALOG: tuple[SimFlightSpec, ...] = (
    SimFlightSpec(
        carrier="XD",
        number="101",
        origin="ATL",
        destination="LAX",
        base_price=220.0,
        amplitude=0.18,
        seed=11,
        duration_minutes=275,
        depart_hour=9,
        depart_minute=15,
    ),
    SimFlightSpec(
        carrier="XD",
        number="202",
        origin="ATL",
        destination="JFK",
        base_price=180.0,
        amplitude=0.22,
        seed=29,
        duration_minutes=140,
        depart_hour=11,
        depart_minute=40,
    ),
    SimFlightSpec(
        carrier="XD",
        number="303",
        origin="ATL",
        destination="LAX",
        base_price=260.0,
        amplitude=0.12,
        seed=47,
        duration_minutes=290,
        depart_hour=16,
        depart_minute=5,
    ),
)

_AIRPORT_NAMES = {
    "ATL": "Hartsfield-Jackson Atlanta International",
    "LAX": "Los Angeles International",
    "JFK": "John F. Kennedy International",
}


def normalize_flight_no(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "".join(value.upper().split())
    return normalized or None


def is_sim(flight_number: str | None) -> bool:
    flight_no = normalize_flight_no(flight_number)
    if flight_no is None:
        return False
    return any(spec.flight_no == flight_no for spec in CATALOG)


def _spec_for(flight_number: str | None) -> SimFlightSpec | None:
    flight_no = normalize_flight_no(flight_number)
    if flight_no is None:
        return None
    for spec in CATALOG:
        if spec.flight_no == flight_no:
            return spec
    return None


def _departure_in_quote_window(departure_date: date, *, today: date | None = None) -> bool:
    day = today or datetime.now(UTC).date()
    protection_days = (departure_date - day).days
    return 1 <= protection_days <= MAX_QUOTE_DAYS


def _unit_noise(key: str) -> float:
    """Deterministic value in [-1, 1] from a string key."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    # Map 53 bits into [0, 1), then to [-1, 1].
    n = int.from_bytes(digest[:7], "big") / float(1 << 56)
    return n * 2.0 - 1.0


def _smoothstep(frac: float) -> float:
    return frac * frac * (3.0 - 2.0 * frac)


def _value_noise_1d(x: float, salt: str) -> float:
    """Smooth 1D value noise; knots every integer x."""
    i = floor(x)
    f = x - i
    u = _smoothstep(f)
    a = _unit_noise(f"{salt}:{i}")
    b = _unit_noise(f"{salt}:{i + 1}")
    return a + (b - a) * u


def price_at(
    flight_number: str,
    *,
    at: datetime | None = None,
) -> float:
    """Live sim price for a catalog flight at wall-clock ``at`` (UTC).

    Layered value noise (not a sine): irregular drifts that stay within
    ``base * (1 ± amplitude)``, changing on a ~``STEP_SECONDS`` timescale.
    """
    spec = _spec_for(flight_number)
    if spec is None:
        raise ValueError(f"Unknown sim flight {flight_number!r}")
    when = at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    else:
        when = when.astimezone(UTC)

    # Dimensionless time; seed offsets each flight onto a different path.
    t = when.timestamp() / STEP_SECONDS + spec.seed * 17.13
    salt = spec.flight_no
    # Three octaves: slow wander + medium wiggle + small jitter.
    n = (
        0.55 * _value_noise_1d(t, f"{salt}:a")
        + 0.30 * _value_noise_1d(t / 2.3, f"{salt}:b")
        + 0.15 * _value_noise_1d(t / 5.1, f"{salt}:c")
    )
    # Soft clip so layered noise rarely exceeds amplitude band.
    n = max(-1.0, min(1.0, n))
    price = spec.base_price * (1.0 + spec.amplitude * n)
    return round(max(price, PRICE_FLOOR), 2)


def _history_points(
    spec: SimFlightSpec, *, at: datetime | None = None
) -> list[SimpleNamespace]:
    """Synthetic history using the same noise path, stepped back in time."""
    when = at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    else:
        when = when.astimezone(UTC)
    points: list[SimpleNamespace] = []
    # Space samples by a fraction of STEP_SECONDS so the chart shows wander.
    sample_gap = STEP_SECONDS / 2.0
    for step in range(HISTORY_STEPS - 1, -1, -1):
        instant = when - timedelta(seconds=sample_gap * step)
        price = price_at(spec.flight_no, at=instant)
        # Label steps as calendar days ending today for chart UIs.
        day = when.date() - timedelta(days=step)
        points.append(
            SimpleNamespace(
                price=int(round(price)),
                iso_date=day.isoformat(),
            )
        )
    return points


@dataclass
class SimPriceInsights:
    lowest_price: int | None
    price_level: str
    typical_price_range: tuple[int, int] | None
    price_history: list[SimpleNamespace]
    estimated_savings: int | None = None
    destination_city: str | None = None
    flight_price: int | None = None


def history_insights(
    flight_number: str,
    *,
    origin: str,
    destination: str,
    departure_date: date,
    at: datetime | None = None,
) -> SimPriceInsights:
    spec = _spec_for(flight_number)
    if spec is None:
        raise ValueError(f"Unknown sim flight {flight_number!r}")
    if (
        spec.origin != origin.upper()
        or spec.destination != destination.upper()
    ):
        raise ValueError("Sim flight does not match origin/destination")

    points = _history_points(spec, at=at)
    prices = [int(point.price) for point in points]
    low = min(prices)
    high = max(prices)
    current = prices[-1]
    mid = (low + high) / 2
    if current <= mid * 0.95:
        level = "low"
    elif current >= mid * 1.05:
        level = "high"
    else:
        level = "typical"
    city = {
        "LAX": "Los Angeles",
        "JFK": "New York",
        "ATL": "Atlanta",
    }.get(destination.upper(), destination.upper())
    return SimPriceInsights(
        lowest_price=low,
        price_level=level,
        typical_price_range=(low, high),
        price_history=points,
        estimated_savings=max(0, int(round(mid - current))),
        destination_city=city,
        flight_price=current,
    )


def _airport(code: str) -> SimpleNamespace:
    return SimpleNamespace(code=code, name=_AIRPORT_NAMES.get(code, code))


def _clock(
    departure_date: date, hour: int, minute: int, *, add_minutes: int = 0
) -> SimpleNamespace:
    base = datetime(
        departure_date.year,
        departure_date.month,
        departure_date.day,
        hour,
        minute,
        tzinfo=UTC,
    ) + timedelta(minutes=add_minutes)
    return SimpleNamespace(
        date=(base.year, base.month, base.day),
        time=(base.hour, base.minute),
    )


def _as_itinerary(spec: SimFlightSpec, departure_date: date, *, at: datetime | None) -> SimpleNamespace:
    price = price_at(spec.flight_no, at=at)
    depart = _clock(departure_date, spec.depart_hour, spec.depart_minute)
    arrive = _clock(
        departure_date,
        spec.depart_hour,
        spec.depart_minute,
        add_minutes=spec.duration_minutes,
    )
    leg = SimpleNamespace(
        flight_number=f"{spec.carrier} {spec.number}",
        codeshares=[],
        from_airport=_airport(spec.origin),
        to_airport=_airport(spec.destination),
        departure=depart,
        arrival=arrive,
        duration=spec.duration_minutes,
    )
    return SimpleNamespace(
        price=price,
        airlines=[spec.airline_name],
        flights=[leg],
        is_sim=True,
    )


def iter_itineraries(
    *,
    origin: str,
    destination: str,
    departure_date: date,
    flight_number: str | None = None,
    at: datetime | None = None,
    today: date | None = None,
) -> list[SimpleNamespace]:
    """Return duck-typed itineraries matching OD/date (and optional flight filter)."""
    if not _departure_in_quote_window(departure_date, today=today):
        return []

    origin_u = origin.strip().upper()
    dest_u = destination.strip().upper()
    wanted = normalize_flight_no(flight_number)

    results: list[SimpleNamespace] = []
    for spec in CATALOG:
        if spec.origin != origin_u or spec.destination != dest_u:
            continue
        if wanted is not None and spec.flight_no != wanted:
            continue
        results.append(_as_itinerary(spec, departure_date, at=at))
    return results
