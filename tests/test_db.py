"""Database bootstrap upgrades."""

from sqlalchemy import create_engine, inspect, text

from app.db import migrate_legacy_locks_schema


def test_legacy_locks_table_gets_the_auth_ownership_column():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE locks (
                    id INTEGER PRIMARY KEY,
                    route VARCHAR(7) NOT NULL
                )
                """
            )
        )

    migrate_legacy_locks_schema(engine)

    assert "user_id" in {
        column["name"] for column in inspect(engine).get_columns("locks")
    }
    assert "ix_locks_user_id" in {
        index["name"] for index in inspect(engine).get_indexes("locks")
    }
