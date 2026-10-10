from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

#schema that sent by the client
from app.schemas import FlightSearchRequest
from app.services.fastflight import search_flights

router = APIRouter(prefix="/quotes", tags=["quotes"])

@router.post("/search")
async def search_quote(request: FlightSearchRequest) -> dict[str, object]:
    result = await run_in_threadpool(
        search_flights,
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        adults=request.adults,
        children=request.children,
        infants_in_seat=request.infants_in_seat,
        infants_on_lap=request.infants_on_lap,
        seat=request.seat,
        currency=request.currency,
        language=request.language,
        flight_number=request.flight_number,
    )
    return {"results": result}