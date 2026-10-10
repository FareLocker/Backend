"""Read-only market data from stored fare observations and model fits."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import ROUTES
from app.db import get_db
from app.models import CurveFit, FareObservation, VolFit
from app.schemas import (
    MarketCurve,
    MarketDetail,
    MarketFare,
    MarketOverview,
    MarketProjection,
    MarketRoute,
    MarketVolatility,
    MarketVolatilityBucket,
)
from app.services.curves import make_f

router = APIRouter(prefix="/market", tags=["market"])
DbSession = Annotated[Session, Depends(get_db)]


def _today() -> date:
    return date.today()


def _fare(row: FareObservation) -> MarketFare:
    return MarketFare(
        price=row.price,
        departure_date=row.departure_date,
        queried_at=row.queried_at,
        airline=row.airline,
        flight_no=row.flight_no,
        cabin=row.cabin,
    )


def _latest_curve(route: str, db: Session) -> CurveFit | None:
    return db.scalars(
        select(CurveFit)
        .where(CurveFit.route == route)
        .order_by(CurveFit.fitted_at.desc(), CurveFit.id.desc())
        .limit(1)
    ).first()


def _latest_volatility(route: str, db: Session) -> list[VolFit]:
    latest = db.scalars(
        select(VolFit)
        .where(VolFit.route == route)
        .order_by(VolFit.fitted_at.desc(), VolFit.id.desc())
        .limit(1)
    ).first()
    if latest is None:
        return []
    return list(
        db.scalars(
            select(VolFit)
            .where(VolFit.route == route, VolFit.fitted_at == latest.fitted_at)
            .order_by(VolFit.bucket_lo, VolFit.id)
        )
    )


@router.get("", response_model=MarketOverview)
def market_overview(db: DbSession) -> MarketOverview:
    today = _today()
    routes = []
    for origin, destination in ROUTES:
        route = f"{origin}-{destination}"
        observation = db.scalars(
            select(FareObservation)
            .where(
                FareObservation.origin == origin,
                FareObservation.dest == destination,
                FareObservation.departure_date >= today,
            )
            .order_by(FareObservation.queried_at.desc(), FareObservation.id.desc())
            .limit(1)
        ).first()
        curve = _latest_curve(route, db)
        volatility = _latest_volatility(route, db)
        routes.append(
            MarketRoute(
                origin=origin,
                destination=destination,
                latest_fare=_fare(observation) if observation else None,
                curve_fitted_at=curve.fitted_at if curve else None,
                volatility_fitted_at=volatility[0].fitted_at if volatility else None,
            )
        )
    return MarketOverview(routes=routes)


@router.get("/{origin}/{destination}", response_model=MarketDetail)
def market_detail(
    origin: str,
    destination: str,
    db: DbSession,
    departure_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MarketDetail:
    origin, destination = origin.upper(), destination.upper()
    if (origin, destination) not in ROUTES:
        raise HTTPException(status_code=404, detail="Unsupported route")

    statement = select(FareObservation).where(
        FareObservation.origin == origin, FareObservation.dest == destination
    )
    if departure_date is not None:
        statement = statement.where(FareObservation.departure_date == departure_date)
    observations = db.scalars(
        statement.order_by(FareObservation.queried_at.desc(), FareObservation.id.desc())
        .limit(limit)
    ).all()

    route = f"{origin}-{destination}"
    curve_fit = _latest_curve(route, db)
    curve = None
    if curve_fit is not None:
        today = _today()
        if departure_date is None:
            dates = [today + timedelta(days=d) for d in range(1, 61)]
        elif 1 <= (departure_date - today).days <= 60:
            dates = [departure_date]
        else:
            dates = []
        f = make_f(curve_fit.params)
        curve = MarketCurve(
            fitted_at=curve_fit.fitted_at,
            projections=[
                MarketProjection(
                    departure_date=day,
                    estimated_fare=f((day - today).days, day.weekday(), 0),
                )
                for day in dates
            ],
        )

    vol_fits = _latest_volatility(route, db)
    volatility = (
        MarketVolatility(
            fitted_at=vol_fits[0].fitted_at,
            buckets=[
                MarketVolatilityBucket(
                    bucket_lo=row.bucket_lo,
                    bucket_hi=row.bucket_hi,
                    sigma=row.sigma,
                    n_obs=row.n_obs,
                    shrink_w=row.shrink_w,
                )
                for row in vol_fits
            ],
        )
        if vol_fits
        else None
    )
    return MarketDetail(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        observations=[_fare(row) for row in observations],
        curve=curve,
        volatility=volatility,
    )
