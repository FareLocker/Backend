"""Database entities for FareLocker.

Prices are stored as ``Decimal`` values through SQL ``NUMERIC`` columns. A
fare snapshot is immutable evidence of a price at a point in time; a lock
copies the contract terms that were offered to the customer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class imported by the database setup and Alembic."""


class LockStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLAIMABLE = "claimable"
    PAID = "paid"
    CANCELLED = "cancelled"


class Flight(Base):
    """The itinerary whose fare is protected.

    One row represents a single searchable itinerary or offer rather than an
    airline's repeating flight number. ``provider_offer_id`` can hold the
    provider's stable offer identifier when a live provider is used.
    """

    __tablename__ = "flights"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    provider_offer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    origin: Mapped[str] = mapped_column(String(3), index=True)
    destination: Mapped[str] = mapped_column(String(3), index=True)
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    airline: Mapped[str | None] = mapped_column(String(100))
    flight_number: Mapped[str | None] = mapped_column(String(20))
    cabin_class: Mapped[str] = mapped_column(String(30), default="economy")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    fare_snapshots: Mapped[list[FareSnapshot]] = relationship(
        back_populates="flight", cascade="all, delete-orphan"
    )
    locks: Mapped[list[FareLock]] = relationship(back_populates="flight")


class FareSnapshot(Base):
    """An observed price for a flight, from a provider or the demo simulator."""

    __tablename__ = "fare_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    flight_id: Mapped[UUID] = mapped_column(ForeignKey("flights.id"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    source: Mapped[str] = mapped_column(String(50))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    flight: Mapped[Flight] = relationship(back_populates="fare_snapshots")


class FareLock(Base):
    """A purchased contract protecting the price of one itinerary."""

    __tablename__ = "fare_locks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    flight_id: Mapped[UUID] = mapped_column(ForeignKey("flights.id"), index=True)
    customer_email: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=LockStatus.ACTIVE.value, index=True
    )
    locked_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    premium: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    payout_cap: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    volatility: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    flight: Mapped[Flight] = relationship(back_populates="locks")
    ledger_entries: Mapped[list[LedgerEntry]] = relationship(
        back_populates="lock", cascade="all, delete-orphan"
    )


class LedgerEntry(Base):
    """An immutable record of premium collected or a payout owed."""

    __tablename__ = "ledger_entries"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    lock_id: Mapped[UUID] = mapped_column(ForeignKey("fare_locks.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lock: Mapped[FareLock] = relationship(back_populates="ledger_entries")
