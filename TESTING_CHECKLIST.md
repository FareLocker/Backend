# Testing Checklist for Fast-Flights Integration

## What Was Done

## How to Test

### Quick Validation (30 seconds)
```bash
# Run unit tests (no network needed)
cd Backend
pytest tests/test_fastflight.py -v
```

Expected output: All tests pass

---

### Test Real Google Flights API (2 minutes)
```bash
python test_integration.py
```

---

### Test Full Endpoint (1 minute)
```bash
python test_api_manual.py
```

---

### Test via HTTP (Full Stack)

1. **Start server:**
   ```bash
   uvicorn app.main:app --reload
   ```

2. **Visit interactive docs:**
   - Open: http://localhost:8000/docs
   - Find: `POST /quotes/search`
   - Click "Try it out"

3. **Use this test payload:**
   ```json
   {
     "origin": "ATL",
     "destination": "LAX",
     "departure_date": "2026-11-15",
     "adults": 2,
     "children": 1,
     "infants_on_lap": 1,
     "seat": "economy",
     "currency": "USD",
     "language": "en-US",
     "context": {
       "user_id": "test123",
       "search_type": "vacation"
     }
   }
   ```

4. **Expected response:**
   ```json
   {
     "results": [
       { /* flight data */ },
       ...
     ],
     "request_context": {
       "user_id": "test123",
       "search_type": "vacation"
     }
   }
   ```

---

## What to Look For

### Success Signs
- Returns 20-100 flights
- Each flight has airline, price, times
- Context field is returned in response
- No errors in console

### Warning Signs
- Empty results -> Google may be blocking
- Connection errors -> Network issue
- Import errors -> Dependencies missing


