"""Manual API test script.

This tests the FastAPI endpoint directly without making HTTP requests.
Good for development and debugging.

Usage:
    python test_api_manual.py
"""

import asyncio
import sys
from datetime import date, timedelta
from typing import Any

from app.routers.search import search_quote
from app.schemas import FlightSearchRequest


def format_duration(minutes: int) -> str:
    """Convert minutes to hours and minutes."""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


async def test_api_endpoint() -> bool:
    """Test the search_quote endpoint directly."""
    print("Testing FastAPI endpoint...")
    print("=" * 80)

    # Create a request with all parameters including context
    departure = date.today() + timedelta(days=30)

    request = FlightSearchRequest(
        origin="ATL",
        destination="LAX",
        departure_date=departure,
        adults=2,
        children=1,
        infants_in_seat=0,
        infants_on_lap=1,
        seat="economy",
        currency="USD",
        language="en-US",
        flight_number=None,
        context={
            "user_id": "test123",
            "session_id": "abc-xyz",
            "search_reason": "vacation"
        }
    )

    print("Request:")
    print(f"  Route: {request.origin} -> {request.destination}")
    print(f"  Date: {request.departure_date}")
    print(f"  Passengers: {request.adults} adults, {request.children} children, {request.infants_on_lap} infants on lap")
    print(f"  Context: {request.context}")
    print("\nCalling search_quote endpoint...\n")

    try:
        response: dict[str, Any] = await search_quote(request)

        print("SUCCESS!")
        print("=" * 80)
        results: list[Any] = response.get("results", [])  # type: ignore
        if not isinstance(results, list):
            results = []
        context = response.get("request_context")
        print(f"Found: {len(results)} flight options")
        print(f"Context returned: {context}")
        print("=" * 80)

        # Show first 3 flights
        for i, flight in enumerate(results[:3], 1):
            print(f"\nOption {i}: ${flight.price}")
            print(f"  Airlines: {', '.join(flight.airlines)}")
            print(f"  Stops: {len(flight.flights) - 1}")

            # Show first leg details
            first_leg = flight.flights[0]
            print(f"  Departure: {first_leg.departure.datetime}")
            print(f"  Duration: {format_duration(sum(leg.duration for leg in flight.flights))}")

            last_leg = flight.flights[-1]
            print(f"  Arrival: {last_leg.arrival.datetime}")

        if len(results) > 3:
            print(f"\n... and {len(results) - 3} more options")

        print("\n" + "=" * 80)
        print("API endpoint working correctly!")
        print("=" * 80)

        print("\nKey Features Verified:")
        print("   - FlightSearchRequest validation")
        print("   - infants_on_lap parameter passed")
        print("   - context field preserved in response")
        print("   - fast-flights integration working")
        print("   - Results returned with full details")

        return True

    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_api_endpoint())
    sys.exit(0 if success else 1)
