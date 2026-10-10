# FareLocker API

## Database setup

FareLocker uses PostgreSQL for local development. With PostgreSQL running on
`localhost:5432`, run this command from `Backend`:

```powershell
uv run python scripts/setup_postgres.py
```

The script prompts for the PostgreSQL `postgres` administrator password. It
creates the `farelocker_app` role and `farelocker` database, saves the app's
connection URL in `Backend/.env`, and checks that the app can connect. It keeps
other settings already in `.env`. Re-running it preserves an existing database
and synchronizes the app role's password with `.env`.

If authentication fails, enter the password assigned to the PostgreSQL
`postgres` role when the server was installed. The pgAdmin master password and
your Windows password are separate credentials. The script allows three tries.

Run the API from `Backend`:

```powershell
uv run uvicorn app.main:app --reload
```

## Authentication

FareLocker verifies Supabase access tokens locally through the project's JWKS
endpoint. Create a Supabase project with an asymmetric JWT signing key and
enable Email authentication. Add these values to `Backend/.env`:

```dotenv
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_JWT_AUDIENCE=authenticated
CORS_ORIGINS=http://localhost:3000
```

The web client signs users in through Supabase Auth, then sends the returned
access token as `Authorization: Bearer <access_token>`. `GET /auth/me` verifies
that token and returns the signed-in user's identity. Existing search and
market endpoints remain public.

The API creates tables defined in `app/models.py` when it starts. The current
project does not have a working Alembic configuration; do not use `alembic
upgrade head` for this schema.

## Lock settlement worker

Run a separate process alongside the API:

```powershell
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
