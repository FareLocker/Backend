#!/usr/bin/env python3
"""FareLocker fare scraper: builds an append-only CSV of observed fares.

Grid: directed routes x d=0..30 days-before-departure, one-way economy.
Each run appends one row per (route, departure_date, flight) observed.

CSV columns:
    queried_at, origin, destination, departure_date, d, dow,
    flight, flight_number, codeshares, cabin, price

Conventions:
  - queried_at: UTC ISO 8601, captured per query (not per run).
  - d: days before departure at query time = departure_date - query date.
  - dow: day of week of *departure*, Monday=0 (Python weekday()).
  - flight: semicolon-joined airline name(s) from the itinerary.
  - flight_number: operating flight number from the single direct segment
    (e.g. "DL 412"); key for the delta_flight adjustment.
  - codeshares: semicolon-joined, empty string if none.
  - price: USD float. Rows with unparseable prices are skipped, not invented.
  - Queries use max_stops=0 (direct/nonstop only); multi-segment itineraries
    are also skipped client-side as a safety net.
  - d=0 rows are data for the term-structure fit; lock *quoting* still
    rejects d0 < 1 (a lock cannot outlive departure).

Uses Stephen's modified fast-flights fork. Field access is defensive
(getattr) so the script degrades gracefully if the schema drifts.

Usage:
    python scripts/scrape_fares.py [--out data/fares.csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

from fast_flights import FlightQuery, Passengers, ResultList, create_query, get_flights

from app.constants import ROUTES

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Directed pairs: ATL<->JFK etc. are distinct routes in the term-structure fit.

D_MIN = 0
D_MAX = 30  # inclusive
CABIN = "economy"
SLEEP_SECONDS = 2.0  # politeness delay between queries
MAX_RETRIES = 1

CSV_COLUMNS = [
    "queried_at",
    "origin",
    "destination",
    "departure_date",
    "d",
    "dow",
    "flight",
    "flight_number",
    "codeshares",
    "cabin",
    "price",
]

_PRICE_RE = re.compile(r"[^\d.]")


def parse_price(raw: object) -> float | None:
    """Parse a scraper price like '$1,234' or '1234 USD' to float USD.

    Returns None when unparseable -- callers skip the row, never invent one.
    """
    if raw is None:
        return None
    cleaned = _PRICE_RE.sub("", str(raw))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def scrape_query(origin: str, destination: str, departure: date) -> list[dict]:
    """Scrape one (route, departure_date) cell. Returns row dicts (no I/O)."""
    queried_at = datetime.now(timezone.utc).isoformat()
    query = create_query(
        flights=[
            FlightQuery(
                date=departure.isoformat(),
                from_airport=origin,
                to_airport=destination,
            ),
        ],
        seat="economy",
        trip="one-way",
        passengers=Passengers(adults=1),
        language="en-US",
        max_stops=0,  # Direct / nonstop only
    )
    result: ResultList = get_flights(query)

    d = (departure - datetime.now(timezone.utc).date()).days
    rows: list[dict] = []
    for itinerary in result:
        # Each Flights object is an itinerary; segments live in .flights.
        # Direct = exactly one segment (max_stops=0 should already enforce this).
        segments = getattr(itinerary, "flights", None) or []
        if len(segments) != 1:
            continue
        segment = segments[0]

        price = parse_price(getattr(itinerary, "price", None))
        if price is None:
            print(
                f"  skip: unparseable price {getattr(itinerary, 'price', None)!r} "
                f"for {origin}->{destination} {departure}",
                file=sys.stderr,
            )
            continue

        airlines = getattr(itinerary, "airlines", None) or []
        if isinstance(airlines, str):
            airlines = [airlines]

        codeshares = getattr(segment, "codeshares", None) or []
        if isinstance(codeshares, str):
            codeshares = [codeshares]

        rows.append(
            {
                "queried_at": queried_at,
                "origin": origin,
                "destination": destination,
                "departure_date": departure.isoformat(),
                "d": d,
                "dow": departure.weekday(),
                "flight": ";".join(airlines),
                "flight_number": getattr(segment, "flight_number", "") or "",
                "codeshares": ";".join(codeshares),
                "cabin": CABIN,
                "price": price,
            }
        )
    return rows


def scrape_grid(
    out_path: str, d_min: int, d_max: int, sleep_seconds: float = SLEEP_SECONDS
) -> tuple[int, int]:
    """Run one full grid pass, appending every observation to out_path.

    Append-only: repeated runs build the time series the vol estimator
    needs. Returns (rows_appended, failed_cells).
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write_header = not os.path.exists(out_path) or os.path.getsize(out_path) == 0

    today = datetime.now(timezone.utc).date()
    n_cells = len(ROUTES) * (d_max - d_min + 1)
    print(
        f"Scraping {len(ROUTES)} routes x d={d_min}..{d_max} "
        f"({n_cells} queries) -> {out_path}"
    )

    n_rows, n_fail = 0, 0
    with open(out_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        for origin, destination in ROUTES:
            for d in range(d_min, d_max + 1):
                departure = today + timedelta(days=d)
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        rows = scrape_query(origin, destination, departure)
                        break
                    except Exception as exc:  # noqa: BLE001 -- keep grid moving
                        if attempt >= MAX_RETRIES:
                            print(
                                f"  FAIL {origin}->{destination} d={d}: {exc}",
                                file=sys.stderr,
                            )
                            n_fail += 1
                            rows = []
                        else:
                            time.sleep(sleep_seconds)
                for row in rows:
                    writer.writerow(row)
                    n_rows += 1
                fh.flush()
                print(f"  {origin}->{destination} d={d}: {len(rows)} flights")
                time.sleep(sleep_seconds)
    return n_rows, n_fail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/fares.csv", help="CSV path")
    ap.add_argument("--d-min", type=int, default=D_MIN)
    ap.add_argument("--d-max", type=int, default=D_MAX)
    ap.add_argument("--sleep", type=float, default=SLEEP_SECONDS)
    args = ap.parse_args()

    n_rows, n_fail = scrape_grid(args.out, args.d_min, args.d_max, args.sleep)
    print(f"Done: {n_rows} rows appended, {n_fail} failed cells.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
