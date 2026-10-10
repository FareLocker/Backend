from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.jobs import start_jobs, stop_jobs
from app.routers.search import router as search_router
from app import db
from app.services.ingest import ingest_fares_csv
from app.config import settings


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
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


app.include_router(search_router)
