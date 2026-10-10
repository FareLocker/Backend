"""Load fare observations from CSV into the DB (idempotent)."""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import FareObservation

log = logging.getLogger("farelocker.ingest")

BATCH_SIZE = 1000


def _parse_queried_at(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def csv_row_to_values(row: dict) -> dict | None:
    """Map scraper CSV columns -> FareObservation column dict.

    Returns None if required fields are missing/invalid.
    """
    try:
        price = float(row["price"])
        flight_no = (row.get("flight_number") or "").strip() or None
        return {
            "queried_at": _parse_queried_at(row["queried_at"]),
            "origin": row["origin"].strip().upper(),
            "dest": row["destination"].strip().upper(),
            "departure_date": date.fromisoformat(row["departure_date"]),
            "d": int(row["d"]),
            "dow": int(row["dow"]),
            "airline": (row.get("flight") or "").strip() or None,
            "cabin": (row.get("cabin") or "economy").strip() or "economy",
            "flight_no": flight_no,
            "price": price,
            "codeshares": (row.get("codeshares") or "").strip() or None,
        }
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("skip bad csv row: %s (%s)", row, exc)
        return None


def insert_observations(db: Session, values_list: list[dict]) -> int:
    """Insert rows; skip conflicts on uq_fare_obs_natural. Returns inserted count."""
    if not values_list:
        return 0

    inserted = 0
    for i in range(0, len(values_list), BATCH_SIZE):
        batch = values_list[i : i + BATCH_SIZE]
        stmt = (
            insert(FareObservation)
            .values(batch)
            .on_conflict_do_nothing(constraint="uq_fare_obs_natural")
        )
        result = db.execute(stmt)
        inserted += result.rowcount or 0
    db.commit()
    return inserted


def persist_scrape_rows(rows: list[dict], db: Session) -> int:
    """Map scraper row dicts (CSV-shaped) into DB; skip conflicts."""
    values_list = []
    for row in rows:
        vals = csv_row_to_values(row)
        if vals is not None:
            values_list.append(vals)
    return insert_observations(db, values_list)


def ingest_fares_csv(path: str | Path, db: Session | None = None) -> int:
    """Load data/fares.csv into fare_observations (existing natural keys skipped)."""
    path = Path(path)
    if not path.is_file():
        log.info("no fares csv at %s; skip ingest", path)
        return 0

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        values_list: list[dict] = []
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                vals = csv_row_to_values(row)
                if vals is not None:
                    values_list.append(vals)

        n = insert_observations(db, values_list)
        log.info("ingest %s: %d new rows (%d csv rows read)", path, n, len(values_list))
        return n
    finally:
        if own_session:
            db.close()
