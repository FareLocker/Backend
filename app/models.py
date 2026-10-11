from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FareObservation(Base):
    __tablename__ = "fare_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    queried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    origin: Mapped[str] = mapped_column(String(3))
    dest: Mapped[str] = mapped_column(String(3))
    departure_date: Mapped[date] = mapped_column(Date, index=True)
    d: Mapped[int] = mapped_column(Integer)
    dow: Mapped[int] = mapped_column(Integer)
    airline: Mapped[str | None] = mapped_column(String(50))
    flight_no: Mapped[str | None] = mapped_column(Text)
    cabin: Mapped[str] = mapped_column(String(20), default="economy")
    price: Mapped[float] = mapped_column(Float)
    codeshares: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_fare_obs_route", "origin", "dest"),
        UniqueConstraint(
            "queried_at",
            "origin",
            "dest",
            "departure_date",
            "flight_no",
            "price",
            name="uq_fare_obs_natural",
        ),
    )


class CurveFit(Base):
    __tablename__ = "curve_fits"

    id: Mapped[int] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(7), index=True)  # 'ATL-JFK'
    fitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    params: Mapped[dict] = mapped_column(JSON)  # fit_route_curve output
    flight_premia: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {flight_no: delta}


class VolFit(Base):
    __tablename__ = "vol_fits"

    id: Mapped[int] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(7), index=True)
    fitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bucket_lo: Mapped[int] = mapped_column(Integer)
    bucket_hi: Mapped[int] = mapped_column(Integer)
    sigma: Mapped[float] = mapped_column(Float)
    n_obs: Mapped[int] = mapped_column(Integer)
    shrink_w: Mapped[float] = mapped_column(Float)


class Lock(Base):
    __tablename__ = "locks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    route: Mapped[str] = mapped_column(String(7), index=True)
    flight_no: Mapped[str | None] = mapped_column(String(10))
    departure_date: Mapped[date] = mapped_column(Date, index=True)
    K: Mapped[float] = mapped_column(Float)
    T_days: Mapped[int] = mapped_column(Integer)
    d0: Mapped[int] = mapped_column(Integer)
    model_value: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    lam: Mapped[float] = mapped_column(Float)  # inventory loading used
    sigma_used: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="open")
    # open | exercised | expired | pending_verification
    bought_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settle_fare: Mapped[float | None] = mapped_column(Float)
    payout: Mapped[float | None] = mapped_column(Float)


class FareSnapshot(Base):
    """Latest normalized live itinerary, retained for the detail page."""

    __tablename__ = "fare_snapshots"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    entry_type: Mapped[str] = mapped_column(
        String(30)
    )  # recharge | lock_fee | payout | withdrawal
    note: Mapped[str | None] = mapped_column(Text)
    lock_id: Mapped[int | None] = mapped_column(
        ForeignKey("locks.id"), nullable=True, index=True
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100), unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    hashed_password: Mapped[str] = mapped_column(String(100))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Spectrum's project-user identity is separate from the traveler's own
    # phone number.  Keep the onboarding state durable so a worker restart
    # cannot lose a pending START welcome.
    photon_user_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    photon_assigned_phone_number: Mapped[str | None] = mapped_column(String(20))
    photon_messaging_status: Mapped[str] = mapped_column(
        String(30), default="unavailable", server_default="unavailable"
    )
