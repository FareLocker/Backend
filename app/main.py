from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.config import settings
from app.jobs import start_jobs, stop_jobs
from app.routers.auth import router as auth_router
from app.routers.fares import router as fares_router
from app.routers.market import router as market_router
from app.routers.quotes import router as quotes_router
from app.routers.search import router as search_router
from app.routers.wallet import router as wallet_router
from app.services.ingest import ingest_fares_csv


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    ingest_fares_csv(settings.fares_csv)
    start_jobs()
    try:
        yield
    finally:
        stop_jobs()


app = FastAPI(title="FareLocker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


app.include_router(search_router)
app.include_router(market_router)
app.include_router(auth_router)
app.include_router(quotes_router)
app.include_router(wallet_router)
app.include_router(fares_router)
