"""Create the initial FareLocker schema.

Revision ID: 20261009_0001
Revises:
Create Date: 2026-10-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261009_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "flights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_offer_id", sa.String(length=255), nullable=True),
        sa.Column("origin", sa.String(length=3), nullable=False),
        sa.Column("destination", sa.String(length=3), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("airline", sa.String(length=100), nullable=True),
        sa.Column("flight_number", sa.String(length=20), nullable=True),
        sa.Column("cabin_class", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_offer_id"),
    )
    op.create_index("ix_flights_departure_at", "flights", ["departure_at"])
    op.create_index("ix_flights_destination", "flights", ["destination"])
    op.create_index("ix_flights_origin", "flights", ["origin"])

    op.create_table(
        "fare_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flight_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fare_snapshots_flight_id", "fare_snapshots", ["flight_id"])

    op.create_table(
        "fare_locks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flight_id", sa.Uuid(), nullable=False),
        sa.Column("customer_email", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("locked_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("premium", sa.Numeric(10, 2), nullable=False),
        sa.Column("payout_cap", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("volatility", sa.Numeric(8, 6), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["flight_id"], ["flights.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fare_locks_customer_email", "fare_locks", ["customer_email"])
    op.create_index("ix_fare_locks_expires_at", "fare_locks", ["expires_at"])
    op.create_index("ix_fare_locks_flight_id", "fare_locks", ["flight_id"])
    op.create_index("ix_fare_locks_status", "fare_locks", ["status"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lock_id", sa.Uuid(), nullable=False),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lock_id"], ["fare_locks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_lock_id", "ledger_entries", ["lock_id"])


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_lock_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_fare_locks_status", table_name="fare_locks")
    op.drop_index("ix_fare_locks_flight_id", table_name="fare_locks")
    op.drop_index("ix_fare_locks_expires_at", table_name="fare_locks")
    op.drop_index("ix_fare_locks_customer_email", table_name="fare_locks")
    op.drop_table("fare_locks")
    op.drop_index("ix_fare_snapshots_flight_id", table_name="fare_snapshots")
    op.drop_table("fare_snapshots")
    op.drop_index("ix_flights_origin", table_name="flights")
    op.drop_index("ix_flights_destination", table_name="flights")
    op.drop_index("ix_flights_departure_at", table_name="flights")
    op.drop_table("flights")
