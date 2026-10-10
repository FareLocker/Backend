#!/usr/bin/env bash
set -euo pipefail
ROOT="${FARELOCKER_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"
mkdir -p "$ROOT/data" "$ROOT/logs"
exec "$ROOT/.venv/bin/python" scripts/run_scraper.py --out "$ROOT/data/fares.csv" "$@"
