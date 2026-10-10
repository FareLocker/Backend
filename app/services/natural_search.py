

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.schemas import FlightSearchRequest


@dataclass(frozen=True)
class SearchParseError(ValueError):
    """A natural search query could not be resolved safely."""

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


_AIRPORT_ALIASES = {
    "atl": "ATL",
    "atlanta": "ATL",
    "jfk": "JFK",
    "new york": "JFK",
    "new york city": "JFK",
    "lax": "LAX",
    "los angeles": "LAX",
    "mia": "MIA",
    "miami": "MIA",
    "ord": "ORD",
    "chicago": "ORD",
}
_CITY_NAME = "(?:" + "|".join(
    re.escape(alias)
    for alias in sorted(
        (alias for alias in _AIRPORT_ALIASES if len(alias) > 3),
        key=len,
        reverse=True,
    )
) + ")"
_LOCATION = rf"(?:{_CITY_NAME}|[A-Za-z]{{3}})"
_ROUTE_PATTERNS = (
    re.compile(
        rf"\bfrom\s+(?P<origin>{_LOCATION})\s*(?:to|->|→)\s*"
        rf"(?P<destination>{_LOCATION})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?P<origin>{_LOCATION})\s*(?:to|->|→)\s*"
        rf"(?P<destination>{_LOCATION})\b",
        re.IGNORECASE,
    ),
)

_ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_DATE_PATTERN = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_NEXT_WEEKDAY_PATTERN = re.compile(
    r"\bnext\s+(" + "|".join(_WEEKDAYS) + r")\b",
    re.IGNORECASE,
)
_RELATIVE_DATE_PATTERN = re.compile(r"\b(today|tomorrow)\b", re.IGNORECASE)
_FARE_PATTERNS = (
    re.compile(r"\$\s*(?P<fare>\d+(?:\.\d{1,2})?)\b"),
    re.compile(
        r"\b(?:paid|cost|price|fare(?:\s+was)?|for)\s+\$?"
        r"(?P<fare>\d+(?:\.\d{1,2})?)\b",
        re.IGNORECASE,
    ),
)
_FLIGHT_NUMBER_PATTERNS = (
    re.compile(
        r"\bflight\s+(?P<carrier>[A-Za-z]{2})\s*(?P<number>\d{1,4})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?P<carrier>[A-Z]{2})\s*(?P<number>\d{1,4})\b"),
)


def _parse_route(query: str) -> tuple[str, str]:
    routes: set[tuple[str, str]] = set()
    for pattern in _ROUTE_PATTERNS:
        for match in pattern.finditer(query):
            routes.add(
                (
                    _AIRPORT_ALIASES.get(
                        match.group("origin").lower(), match.group("origin").upper()
                    ),
                    _AIRPORT_ALIASES.get(
                        match.group("destination").lower(),
                        match.group("destination").upper(),
                    ),
                )
            )

    if not routes:
        raise SearchParseError(
            "missing_route",
            'Include a route using airport codes or city names, such as "Atlanta to Miami".',
        )
    if len(routes) > 1:
        raise SearchParseError(
            "ambiguous_route",
            "Include exactly one origin and destination route.",
        )
    return next(iter(routes))


def _next_month_day(month: int, day: int, reference_date: date) -> date:
    """Return the next valid occurrence, including the reference date."""

    for year in range(reference_date.year, reference_date.year + 9):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= reference_date:
            return candidate
    raise ValueError("Invalid month and day")


def _parse_departure_date(
    query: str, reference_date: date, *, required: bool = False
) -> date:
    candidates: set[date] = set()

    for match in _ISO_DATE_PATTERN.finditer(query):
        try:
            candidates.add(date.fromisoformat(match.group(1)))
        except ValueError as error:
            raise SearchParseError(
                "invalid_date", "The departure date is not valid."
            ) from error

    for match in _MONTH_DATE_PATTERN.finditer(query):
        month = _MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        year_text = match.group(3)
        try:
            candidate = (
                date(int(year_text), month, day)
                if year_text
                else _next_month_day(month, day, reference_date)
            )
        except ValueError as error:
            raise SearchParseError(
                "invalid_date", "The departure date is not valid."
            ) from error
        candidates.add(candidate)

    for match in _RELATIVE_DATE_PATTERN.finditer(query):
        offset = 0 if match.group(1).lower() == "today" else 1
        candidates.add(reference_date + timedelta(days=offset))

    for match in _NEXT_WEEKDAY_PATTERN.finditer(query):
        target_weekday = _WEEKDAYS[match.group(1).lower()]
        days_ahead = (target_weekday - reference_date.weekday()) % 7
        candidates.add(reference_date + timedelta(days=days_ahead or 7))

    if not candidates and required:
        raise SearchParseError(
            "missing_departure_date",
            'What is the departure date? Reply with a date such as "next Friday".',
        )
    if not candidates:
        return reference_date + timedelta(days=1)
    if len(candidates) > 1:
        raise SearchParseError(
            "ambiguous_date", "Include exactly one departure date."
        )

    departure_date = next(iter(candidates))
    if departure_date < reference_date:
        raise SearchParseError(
            "past_date", "Departure date cannot be in the past."
        )
    return departure_date


def _parse_fare_paid(query: str) -> float:
    fares: set[float] = set()
    for pattern in _FARE_PATTERNS:
        for match in pattern.finditer(query):
            fares.add(float(match.group("fare")))

    if not fares:
        raise SearchParseError(
            "missing_fare_paid",
            'Include the amount paid, such as "$420".',
        )
    if len(fares) > 1:
        raise SearchParseError(
            "ambiguous_fare_paid",
            "Include exactly one fare amount.",
        )

    fare_paid = next(iter(fares))
    if fare_paid <= 0:
        raise SearchParseError("invalid_fare_paid", "Fare paid must be greater than zero.")
    return fare_paid


def _parse_flight_number(query: str) -> str | None:
    matches = set()
    for pattern in _FLIGHT_NUMBER_PATTERNS:
        matches.update(
            f"{match.group('carrier').upper()} {match.group('number')}"
            for match in pattern.finditer(query)
        )
    if len(matches) > 1:
        raise SearchParseError(
            "ambiguous_flight_number",
            "Include at most one flight number.",
        )
    return next(iter(matches)) if matches else None


@dataclass(frozen=True)
class ParsedFareLockQuote:
    origin: str
    destination: str
    departure_date: date
    fare_paid: float
    flight_number: str | None


def parse_natural_farelock_quote(
    query: str, *, reference_date: date | None = None
) -> ParsedFareLockQuote:
    """Resolve a conversational FareLock request into pricing inputs."""

    today = reference_date or date.today()
    origin, destination = _parse_route(query)
    departure_date = _parse_departure_date(query, today, required=True)
    fare_paid = _parse_fare_paid(query)
    flight_number = _parse_flight_number(query)
    return ParsedFareLockQuote(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        fare_paid=fare_paid,
        flight_number=flight_number,
    )


def parse_natural_search(
    query: str, *, reference_date: date | None = None
) -> FlightSearchRequest:
    """Resolve a website search query into the canonical search model."""

    today = reference_date or date.today()
    origin, destination = _parse_route(query)
    departure_date = _parse_departure_date(query, today)
    return FlightSearchRequest(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
    )
