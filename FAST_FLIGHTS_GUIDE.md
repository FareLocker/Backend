
## Current Implementation

### Architecture Flow

```
Client HTTP Request
       ↓
FlightSearchRequest (validation)
       ↓
FastAPI Router (search.py)
       ↓
search_flights() (fastflight.py)
       ↓
fast-flights library
       ↓
Google Flights website
       ↓
Flight Results
```

### Code Breakdown

#### 1. **FlightSearchRequest** (`Backend/app/schemas.py`)
```python
class FlightSearchRequest(BaseModel):
    origin: str                    # "ATL"
    destination: str               # "LAX"
    departure_date: date           # 2026-11-15
    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0        # Now fully supported!
    seat: Literal["economy", ...] = "economy"
    currency: str = "USD"
    language: str = "en-US"
    flight_number: Optional[str] = None
    context: Optional[dict] = None  # Your custom metadata
```

**Purpose**: Validates incoming HTTP requests and provides type safety

#### 2. **search_flights()** (`Backend/app/services/fastflight.py`)
```python
def search_flights(...) -> ResultList:
    # 1. Validates airport codes (must be 3 letters)
    # 2. Validates passenger counts (at least 1 adult)
    # 3. Creates a fast-flights query
    query = create_query(
        flights=[FlightQuery(...)],
        passengers=Passengers(
            adults=adults,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,  # Now passed!
        ),
        ...
    )
    # 4. Calls Google Flights via fast-flights
    return get_flights(query)
```

**Purpose**: Business logic layer that calls the external library

#### 3. **search_quote()** (`Backend/app/routers/search.py`)
```python
@router.post("/search")
async def search_quote(request: FlightSearchRequest):
    result = await run_in_threadpool(search_flights, ...)
    return {
        "results": result,
        "request_context": request.context  # Returns your metadata
    }
```

**Purpose**: HTTP endpoint that ties everything together

---

## How to Test

### 1. Run Unit Tests (No Network)
```bash
cd Backend
pytest tests/test_fastflight.py -v
```

### 2. Test fast-flights Integration (Real API)
```bash
python test_integration.py
```

### 3. Test the FastAPI Endpoint
```bash
python test_api_manual.py
```

This tests your endpoint logic including the `context` field.

### 4. Test via HTTP (Full Stack)

Start the server:
```bash
cd Backend
uvicorn app.main:app --reload
```

Send a request:
```bash
curl -X POST http://localhost:8000/quotes/search \
  -H "Content-Type: application/json" \
  -d '{
    "origin": "ATL",
    "destination": "LAX",
    "departure_date": "2026-11-15",
    "adults": 2,
    "infants_on_lap": 1,
    "context": {"user_id": "12345"}
  }'
```



