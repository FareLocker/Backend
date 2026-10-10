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
        return_date=request.return_date,
        adults=request.adults,
        children=request.children,
        infants_in_seat=request.infants_in_seat,
        infants_on_lap=request.infants_on_lap,
        seat=request.seat,
        currency=request.currency,
        language=request.language,
        max_stops=request.max_stops,
        airlines=request.airlines,
        earliest_departure_hour=request.earliest_departure_hour,
        latest_departure_hour=request.latest_departure_hour,
        earliest_arrival_hour=request.earliest_arrival_hour,
        latest_arrival_hour=request.latest_arrival_hour,
        max_duration_minutes=request.max_duration_minutes,
        connecting_airports=request.connecting_airports,
        min_layover_minutes=request.min_layover_minutes,
        max_layover_minutes=request.max_layover_minutes,
        less_emissions_only=request.less_emissions_only,
        max_price=request.max_price,
        carry_on_bags=request.carry_on_bags,
        checked_bags=request.checked_bags,
        hide_separate_and_self_transfer=request.hide_separate_and_self_transfer,
        exclude_basic_economy=request.exclude_basic_economy,
        flight_number=request.flight_number,
    )
    return {"results": result, "request_context": request.context}
