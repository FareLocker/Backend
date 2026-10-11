# FareLocker API

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and PostgreSQL.

```bash
cp .env.example .env
```

Edit `.env` before running:

| Variable | Purpose |
| --- | --- |
| `POSTGRES_*` | Database connection (defaults match local Docker/`farelocker` DB) |
| `SECRET_KEY` | HS256 JWT signing key — use a long random string (≥32 bytes) |
| `SCRAPER_ENABLED` | Enable background fare scraping when `true` |
| `HOST_UID` / `HOST_GID` | Optional host user mapping for Docker volume permissions |
| `PHOTON_MESSAGING_SECRET` | Shared secret used to authenticate welcome-message requests to Photon; must match the worker's `PHOTON_INTERNAL_SECRET` |
| `PROJECT_ID` / `PROJECT_SECRET` | Spectrum Cloud project credentials used by the Photon worker |

Optional overrides (see `app/config.py`): `CORS_ORIGINS`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `SETTLEMENT_INTERVAL_MINUTES`, `FARES_CSV`.

```bash
uv sync
uv run uvicorn app.main:app --reload
```

The API creates tables from `app/models.py` on startup. It also upgrades legacy
`locks` tables by adding the account-ownership column required by authenticated
lock lookups. Legacy rows remain unassigned until they are explicitly
backfilled; newly purchased locks are assigned automatically. Alembic is
present but not required for local schema bootstrap.

## Authentication

Auth is local email/password (bcrypt) with HS256 JWTs. No Supabase.

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/auth/register` | Body: `email`, `password`, `first_name`, `last_name`, optional `phone_number`. Returns `{ access_token, token_type }`. |
| `POST` | `/auth/login` | Body: `identifier` (email **or** phone) + `password`. Returns a bearer token. |
| `GET` | `/auth/me` | Requires `Authorization: Bearer <access_token>`. |

Search and market endpoints stay public. Wallet routes require a valid bearer token and only allow access to the signed-in user's balance (`User.balance_cents`).

## Lock settlement worker

Run a separate process alongside the API:

```bash
uv run python -m app.jobs
```

The worker checks open and pending locks every 60 minutes by default. Set
`SETTLEMENT_INTERVAL_MINUTES` to change the interval. On the flight day in the
origin airport's timezone, it searches for a USD economy fare for one adult.
If that search yields no matching price, it uses the latest matching stored
fare observed after the lock was bought. When neither exists, the lock stays
`pending_verification` for a later retry. A positive fare increase is stored
as `payout`; payment delivery is not implemented.

Lock creation is currently an internal Python service in
`app.services.locks.create_lock`. It accepts a trusted `PricedLockInput` from
application code. There is no public purchase endpoint until pricing and
payment are connected.
