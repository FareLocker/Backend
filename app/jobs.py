"""Background schedulers for FareLocker API process.

Default: hourly curve + vol refit.
Optional: in-process scrape tiers when settings.scraper_enabled
(leave False if the VPS systemd scraper owns data/fares.csv)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.services.curves import refit_all_routes
from app.services.vol import refit_all_vols

log = logging.getLogger("farelocker.jobs")

_sched: BackgroundScheduler | None = None


def _ensure_scripts_path() -> None:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    p = str(scripts)
    if p not in sys.path:
        sys.path.insert(0, p)


def _safe_refit() -> None:
    try:
        log.info("refit_all_routes starting")
        refit_all_routes()
        log.info("refit_all_vols starting")
        refit_all_vols()
        log.info("refit done")
    except Exception:
        log.exception("refit failed; scheduler continues")


def _make_scrape_job(d_min: int, d_max: int, label: str):
    _ensure_scripts_path()
    from scrape_fares import scrape_grid  # noqa: WPS440 - scripts/ on path

    def job() -> None:
        log.info("scrape %s tier d=%d..%d", label, d_min, d_max)
        try:
            n_rows, n_fail = scrape_grid(
                settings.fares_csv,
                d_min,
                d_max,
                settings.scraper_sleep_seconds,
                write_db=True,
            )
            log.info("scrape %s done: %d rows, %d failed cells", label, n_rows, n_fail)
        except Exception:
            log.exception("scrape %s crashed; scheduler continues", label)

    job.__name__ = f"scrape_{label}"
    return job


def start_jobs() -> BackgroundScheduler:
    """Starts background scheduler; idempotent"""
    global _sched
    if _sched is not None:
        return _sched

    sched = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    minutes = max(int(settings.refit_interval_minutes), 1)
    sched.add_job(_safe_refit, "interval", minutes=minutes, id="refit")

    if settings.scraper_enabled:
        hot = _make_scrape_job(1, 14, "hot")
        full = _make_scrape_job(1, 60, "full")

        sched.add_job(hot, "date", id="scrape_hot_bootstrap")
        sched.add_job(hot, "interval", minutes=30, id="scrape_hot")
        sched.add_job(full, "interval", hours=2, id="scrape_full")
        log.info("scraper enabled: hot q30m, full q2h -> %s", settings.fares_csv)
    else:
        log.info("scraper_enabled=false")

    sched.start()
    _sched = sched
    log.info("jobs started (refit every %dm)", minutes)
    return sched


def stop_jobs() -> None:
    global _sched
    if _sched is None:
        return
    _sched.shutdown(wait=False)
    _sched = None
    log.info("jobs stopped")
