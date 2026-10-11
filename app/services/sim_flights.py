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
# Calendar-day history is evaluated at this UTC hour so past bars stay fixed
# across polls (only "today" tracks the live wall-clock price).
HISTORY_ANCHOR_HOUR_UTC = 12
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


# Two flights per ROUTES pair. XD101 / XD202 / XD303 keep legacy pitch identities.
CATALOG: tuple[SimFlightSpec, ...] = (
    # ATL-JFK
    SimFlightSpec("XD", "202", "ATL", "JFK", 180.0, 0.22, 29, 140, 11, 40),
    SimFlightSpec("XD", "204", "ATL", "JFK", 195.0, 0.16, 31, 145, 17, 20),
    # JFK-ATL
    SimFlightSpec("XD", "211", "JFK", "ATL", 185.0, 0.20, 41, 145, 9, 5),
    SimFlightSpec("XD", "213", "JFK", "ATL", 200.0, 0.15, 43, 150, 16, 45),
    # ATL-LAX
    SimFlightSpec("XD", "101", "ATL", "LAX", 220.0, 0.18, 11, 275, 9, 15),
    SimFlightSpec("XD", "303", "ATL", "LAX", 260.0, 0.12, 47, 290, 16, 5),
    # LAX-ATL
    SimFlightSpec("XD", "111", "LAX", "ATL", 230.0, 0.17, 51, 270, 8, 40),
    SimFlightSpec("XD", "113", "LAX", "ATL", 250.0, 0.14, 53, 280, 15, 55),
    # ATL-MIA
    SimFlightSpec("XD", "301", "ATL", "MIA", 160.0, 0.19, 61, 120, 10, 10),
    SimFlightSpec("XD", "305", "ATL", "MIA", 175.0, 0.15, 63, 125, 18, 0),
    # MIA-ATL
    SimFlightSpec("XD", "311", "MIA", "ATL", 165.0, 0.18, 67, 125, 9, 25),
    SimFlightSpec("XD", "315", "MIA", "ATL", 180.0, 0.14, 69, 130, 17, 35),
    # JFK-LAX
    SimFlightSpec("XD", "401", "JFK", "LAX", 280.0, 0.16, 71, 360, 8, 15),
    SimFlightSpec("XD", "403", "JFK", "LAX", 310.0, 0.13, 73, 355, 16, 50),
    # LAX-JFK
    SimFlightSpec("XD", "411", "LAX", "JFK", 290.0, 0.16, 77, 330, 7, 55),
    SimFlightSpec("XD", "413", "LAX", "JFK", 320.0, 0.12, 79, 335, 15, 20),
    # ATL-ORD
    SimFlightSpec("XD", "501", "ATL", "ORD", 150.0, 0.20, 81, 130, 10, 45),
    SimFlightSpec("XD", "503", "ATL", "ORD", 165.0, 0.15, 83, 135, 18, 15),
    # ORD-ATL
    SimFlightSpec("XD", "511", "ORD", "ATL", 155.0, 0.19, 87, 125, 9, 35),
    SimFlightSpec("XD", "513", "ORD", "ATL", 170.0, 0.14, 89, 130, 17, 5),
)

_AIRPORT_NAMES = {
    "ATL": "Hartsfield-Jackson Atlanta International",
    "LAX": "Los Angeles International",
    "JFK": "John F. Kennedy International",
    "MIA": "Miami International",
    "ORD": "Chicago O'Hare International",
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


def _day_anchor(day: date) -> datetime:
    """Fixed UTC instant used for a completed calendar day's history bar."""
    return datetime(
        day.year,
        day.month,
        day.day,
        HISTORY_ANCHOR_HOUR_UTC,
        0,
        0,
        tzinfo=UTC,
    )


def _history_points(
    spec: SimFlightSpec, *, at: datetime | None = None
) -> list[SimpleNamespace]:
    """Calendar-day history ending today.

    Past days are anchored at ``HISTORY_ANCHOR_HOUR_UTC`` so repeated polls
    during the same UTC day return identical bars. Today's point uses the
    live wall-clock instant so it can track the moving sim fare.
    """
    when = at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    else:
        when = when.astimezone(UTC)
    today = when.date()
    points: list[SimpleNamespace] = []
    for step in range(HISTORY_STEPS - 1, -1, -1):
        day = today - timedelta(days=step)
        instant = when if day == today else _day_anchor(day)
        price = price_at(spec.flight_no, at=instant)
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
        "MIA": "Miami",
        "ORD": "Chicago",
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
