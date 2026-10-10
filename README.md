# FareLocker API

## Database setup

FareLocker uses SQLAlchemy models and Alembic migrations. Local development
defaults to SQLite at `farelocker.db`; set `DATABASE_URL` to use PostgreSQL.

Create or update the database schema before starting the API:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

Useful migration commands:

```powershell
# Create a migration after changing app/models.py
alembic revision --autogenerate -m "describe the schema change"

# Show the installed migration
alembic current

# Roll back one migration
alembic downgrade -1
```

The initial schema stores searchable flight offers, immutable price snapshots,
purchased fare locks, and their financial ledger entries. Monetary values use
fixed-precision decimal columns.
