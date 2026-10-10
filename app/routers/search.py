from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
#Searches from the FastFlight API aswell as natural language search
from app.schemas import FlightSearchRequest, NaturalFlightSearchRequest
from app.services.fastflight import search_flights
from app.services.natural_search import SearchParseError, parse_natural_search

router = APIRouter(prefix="/quotes", tags=["quotes"])

#localhost:8000/quotes/search
@router.post("/search")
#Searches for flight quotes based on the provided request
async def search_quote(request: FlightSearchRequest) -> dict[str, object]:
    result = await _execute_search(request)
    return {"results": result, "request_context": request.context}


async def _execute_search(request: FlightSearchRequest) -> object:
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
    return result


@router.post("/search/natural")
async def search_natural(request: NaturalFlightSearchRequest) -> dict[str, object]:
    try:
        resolved_search = parse_natural_search(request.query)
    except SearchParseError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": error.message},
        ) from error

    result = await _execute_search(resolved_search)
    return {
        "query": request.query,
        "resolved_search": resolved_search.model_dump(
            mode="json", exclude={"context"}
        ),
        "results": result,
    }
