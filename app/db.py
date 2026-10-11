from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from app.config import settings

# Configure engine based on database type
engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    migrate_legacy_locks_schema()
    migrate_users_messaging_schema()


def migrate_legacy_locks_schema(db_engine: Engine = engine) -> None:
    """Bring pre-auth lock tables forward without recreating production data.

    ``create_all`` creates new tables but intentionally never alters existing
    ones. The local-auth release added ``locks.user_id`` after some databases
    already had a locks table, which otherwise makes every authenticated lock
    lookup fail at SQL execution time.
    """
    table_names = inspect(db_engine).get_table_names()
    if "locks" not in table_names:
        return
    columns = {column["name"] for column in inspect(db_engine).get_columns("locks")}
    if "user_id" in columns:
        return

    # Existing locks predate account ownership, so they stay NULL until an
    # operator can safely identify their owner. New purchases always set it.
    with db_engine.begin() as connection:
        connection.execute(text("ALTER TABLE locks ADD COLUMN user_id INTEGER"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_locks_user_id ON locks (user_id)")
        )


def migrate_users_messaging_schema(db_engine: Engine = engine) -> None:
    """Add durable Spectrum onboarding fields to pre-messaging user tables."""
    table_names = inspect(db_engine).get_table_names()
    if "users" not in table_names:
        return
    columns = {column["name"] for column in inspect(db_engine).get_columns("users")}
    additions = {
        "photon_user_id": "VARCHAR(100)",
        "photon_assigned_phone_number": "VARCHAR(20)",
        "photon_messaging_status": "VARCHAR(30) NOT NULL DEFAULT 'unavailable'",
    }
    with db_engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_photon_user_id ON users (photon_user_id)")
        )
