#!/usr/bin/env python3
"""Long-running FareLocker scraper scheduler (for a VPS).

Runs the tiered polling plan, appending every observation to ONE csv:
  - hot tier:  d=1..14, every 30 minutes
  - full grid: d=1..60, every 2 hours

Single-worker scheduler: jobs never overlap -- a late job simply runs after
the previous one finishes, so the CSV is never written concurrently.
The process is stateless (all state is the CSV); safe to restart anytime.

Usage:
    python scripts/run_scraper.py [--out data/fares.csv] [--sleep 2.0]

On a VPS, run under systemd -- see scripts/farelocker-scraper.service.
Logs go to stdout; point them at a file in the unit.
"""

from __future__ import annotations

import argparse
import logging
import sys

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from scrape_fares import scrape_grid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("farelocker.scraper")


def make_job(out: str, d_min: int, d_max: int, sleep: float, label: str):
    def job() -> None:
        log.info("starting %s tier: d=%d..%d", label, d_min, d_max)
        try:
            n_rows, n_fail = scrape_grid(out, d_min, d_max, sleep)
        except Exception:  # noqa: BLE001 -- never kill the scheduler
            log.exception("%s tier crashed; scheduler continues", label)
            return
        log.info("%s tier done: %d rows appended, %d failed cells",
                 label, n_rows, n_fail)

    job.__name__ = f"scrape_{label}"
    return job


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/fares.csv",
                    help="single CSV all tiers append to")
    ap.add_argument("--sleep", type=float, default=2.0,
                    help="seconds between queries")
    args = ap.parse_args()

    hot = make_job(args.out, 1, 14, args.sleep, "hot")
    full = make_job(args.out, 1, 60, args.sleep, "full")

    # Bootstrap: run the hot tier once immediately so data starts flowing.
    log.info("bootstrap hot-tier run -> %s", args.out)
    hot()

    sched = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    sched.add_job(hot, "interval", minutes=30, id="hot_tier")
    sched.add_job(full, "interval", hours=2, id="full_grid")
    log.info("scheduler started: hot q30min (d=1..14), full q2h (d=1..60)")
    sched.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
