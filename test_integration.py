"""Integration test script for fast-flights API.

Run this to verify the fast-flights library is working correctly.

Usage:
    python test_integration.py
"""

import sys
from datetime import date, timedelta
from typing import Any

from app.services.fastflight import search_flights


def format_duration(minutes: int) -> str:
    """Convert minutes to hours and minutes."""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"

def format_datetime(simple_dt: Any) -> str:
    """Format SimpleDatetime to readable string."""
    year, month, day = simple_dt.date  # noqa: F841
    hour, minute = simple_dt.time  # noqa: F841
    return f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"

def test_real_flight_search() -> bool:
    """Test a real flight search using fast-flights."""
    print("Testing fast-flights integration...")
    print("=" * 60)
    
    # Search for flights 30 days from now
    departure = date.today() + timedelta(days=30)
    
    print("\nSearching flights:")
    print("  Route: ATL -> LAX")
    print(f"  Date: {departure}")
    print("  Passengers: 1 adult, 1 child, 1 infant on lap")
    print("  Class: Economy")
    print("\nMaking API call to Google Flights...\n")
    
    try:
        results = search_flights(
            origin="ATL",
            destination="LAX",
            departure_date=departure,
            adults=1,
            children=1,
            infants_on_lap=1,
            seat="economy",
            currency="USD",
            language="en-US",
        )
        
        print(f"SUCCESS! Found {len(results)} flights\n")
        print("=" * 80)
        print("Flight Details:")
        print("=" * 80)
        
        # Print first 5 results with proper formatting
        for i, flight in enumerate(results[:5], 1):
            print(f"\n{'='*80}")
            print(f"Option {i}: ${flight.price} USD")
            print(f"{'='*80}")
            
            # flight.airlines is a list of airline codes
            print(f"Airlines: {', '.join(flight.airlines)}")
            print(f"Type: {flight.type} ({len(flight.flights)} leg(s))")
            
            # Calculate total duration
            total_duration = sum(leg.duration for leg in flight.flights)
            print(f"Total Duration: {format_duration(total_duration)}")
            
            # Each flight can have multiple legs (connecting flights)
            for leg_num, leg in enumerate(flight.flights, 1):
                if len(flight.flights) > 1:
                    print(f"\n  Leg {leg_num}:")
                else:
                    print("\n  Flight:")
                    
                print(f"     {leg.from_airport.code} -> {leg.to_airport.code}")
                print(f"     From: {leg.from_airport.name}")
                print(f"     To:   {leg.to_airport.name}")
                print(f"     Departs: {format_datetime(leg.departure)}")
                print(f"     Arrives: {format_datetime(leg.arrival)}")
                print(f"     Duration: {format_duration(leg.duration)}")
                print(f"     Aircraft: {leg.plane_type}")
            
            # Carbon emissions
            print("\n  Carbon Footprint:")
            print(f"     This flight: {flight.carbon.emission:,}g CO2")
            print(f"     Typical: {flight.carbon.typical_on_route:,}g CO2")
            diff_pct = ((flight.carbon.emission - flight.carbon.typical_on_route) / flight.carbon.typical_on_route * 100) if flight.carbon.typical_on_route > 0 else 0
            print(f"     {diff_pct:+.1f}% vs typical")
            
        if len(results) > 5:
            print(f"\n{'='*80}")
            print(f"... and {len(results) - 5} more flight options available")
        
        print("\n" + "=" * 80)
        print("fast-flights is working correctly!")
        print("=" * 80)
        
        # Show the raw structure for developers
        print("\nData Structure Reference:")
        print("   - results: ResultList (list of Flights objects)")
        print("   - each Flights has: price, type, airlines, flights (legs), carbon")
        print("   - each SingleFlight (leg) has:")
        print("       * airports (code, name)")
        print("       * departure/arrival (SimpleDatetime with date & time tuples)")
        print("       * duration (minutes), plane_type")
        
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}")
        print("\nPossible issues:")
        print("  - Network connection problem")
        print("  - fast-flights library not installed")
        print("  - Google Flights API changed")
        print("  - Invalid airport codes or date")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_real_flight_search()
    sys.exit(0 if success else 1)
