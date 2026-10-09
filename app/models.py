from datetime import datetime
from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
    route: Mapped[str] = mapped_column(String(7), index=True)
    flight_no: Mapped[str | None] = mapped_column(String(10))
    departure_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
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
