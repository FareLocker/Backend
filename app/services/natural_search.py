from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta, datetime
from dateutil import parser as date_parser

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
# Longest first so "new york city" wins over "new york".
_ALIAS_BY_LENGTH = tuple(sorted(_AIRPORT_ALIASES, key=len, reverse=True))
_KNOWN_IATA = frozenset(_AIRPORT_ALIASES.values()) | frozenset(
    alias.upper()
    for alias in _AIRPORT_ALIASES
    if len(alias) == 3 and alias.isalpha()
)
_CITY_NAME = (
    "(?:"
    + "|".join(
        re.escape(alias)
        for alias in sorted(
            (alias for alias in _AIRPORT_ALIASES if len(alias) > 3),
            key=len,
            reverse=True,
        )
    )
    + ")"
)
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
    # Compact forms: ATL/JFK, ATL-JFK
    re.compile(
        rf"\b(?P<origin>{_LOCATION})\s*[/-]\s*(?P<destination>{_LOCATION})\b",
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

_MONTH_NAMES = (
    "jan|january|feb|february|mar|march|apr|april|may|jun|june|"
    "jul|july|aug|august|sep|sept|september|oct|october|"
    "nov|november|dec|december"
)
_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
# Used only to detect / count date mentions (dateutil does the real parse).
_DATE_MENTION_PATTERNS = (
    re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4})(?!\d)"),
    re.compile(
        rf"\b(?:{_MONTH_NAMES})\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:today|tomorrow)\b", re.IGNORECASE),
    re.compile(rf"\bnext\s+(?:{_WEEKDAYS})\b", re.IGNORECASE),
)

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _normalize_location(raw: str, *, known_only: bool = False) -> str | None:
    key = raw.lower().strip()
    if key in _AIRPORT_ALIASES:
        return _AIRPORT_ALIASES[key]
    if not re.fullmatch(r"[A-Za-z]{3}", raw):
        return None
    code = raw.upper()
    if known_only and code not in _KNOWN_IATA:
        return None
    return code


def _mask_date_spans(query: str) -> str:
    """Blank out date phrases so route scanning ignores them."""
    chars = list(query)
    for pattern in _DATE_MENTION_PATTERNS:
        for match in pattern.finditer(query):
            for index in range(match.start(), match.end()):
                chars[index] = " "
    return "".join(chars)


def _location_mentions(query: str) -> list[str]:
    """IATA codes in left-to-right order (known aliases / codes only)."""
    text = _mask_date_spans(query)
    occupied: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []

    for alias in _ALIAS_BY_LENGTH:
        for match in re.finditer(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
            start, end = match.start(), match.end()
            if any(start < other_end and end > other_start for other_start, other_end in occupied):
                continue
            occupied.append((start, end))
            hits.append((start, _AIRPORT_ALIASES[alias]))

    for match in re.finditer(r"\b[A-Za-z]{3}\b", text):
        start, end = match.start(), match.end()
        if any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        code = _normalize_location(match.group(0), known_only=True)
        if code is None:
            continue
        occupied.append((start, end))
        hits.append((start, code))

    hits.sort(key=lambda item: item[0])
    return [code for _, code in hits]


def _routes_from_connectors(query: str) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for pattern in _ROUTE_PATTERNS:
        for match in pattern.finditer(query):
            origin = _normalize_location(match.group("origin"))
            destination = _normalize_location(match.group("destination"))
            if origin is None or destination is None:
                continue
            routes.add((origin, destination))
    return routes


def _parse_route(query: str) -> tuple[str, str]:
    routes = _routes_from_connectors(query)
    if len(routes) > 1:
        raise SearchParseError(
            "ambiguous_route",
            "Include exactly one origin and destination route.",
        )
    if len(routes) == 1:
        origin, destination = next(iter(routes))
    else:
        codes = _location_mentions(query)
        if len(codes) < 2:
            raise SearchParseError(
                "missing_route",
                'Include a route using airport codes or city names, such as "Atlanta to Miami".',
            )
        if len(codes) > 2:
            raise SearchParseError(
                "ambiguous_route",
                "Include exactly one origin and destination route.",
            )
        origin, destination = codes[0], codes[1]

    if origin == destination:
        raise SearchParseError(
            "invalid_route",
            "Origin and destination must be different airports.",
        )
    return origin, destination


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


def _date_mentions(query: str) -> list[str]:
    spans: list[tuple[int, int, str]] = []
    for pattern in _DATE_MENTION_PATTERNS:
        for match in pattern.finditer(query):
            spans.append((match.start(), match.end(), match.group(0)))
    # Drop mentions fully contained in a longer one.
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[str] = []
    occupied: list[tuple[int, int]] = []
    for start, end, text in spans:
        if any(start >= a and end <= b for a, b in occupied):
            continue
        occupied.append((start, end))
        kept.append(text)
    return kept


def _has_explicit_year(mention: str) -> bool:
    return bool(re.search(r"\b(?:\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", mention))


def _parse_relative_mention(mention: str, reference_date: date) -> date | None:
    """dateutil cannot parse today / tomorrow / next <weekday>."""
    text = mention.strip().lower()
    if text == "today":
        return reference_date
    if text == "tomorrow":
        return reference_date + timedelta(days=1)
    match = re.fullmatch(
        r"next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text
    )
    if not match:
        return None
    target = _WEEKDAY_INDEX[match.group(1)]
    days_ahead = (target - reference_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # "next Friday" on Friday → following week
    return reference_date + timedelta(days=days_ahead)


def _parse_departure_date(
    query: str, reference_date: date, *, required: bool = False
) -> date:
    mentions = _date_mentions(query)
    if not mentions:
        if required:
            raise SearchParseError(
                "missing_departure_date",
                'What is the departure date? Reply with a date such as "next Friday".',
            )
        return reference_date + timedelta(days=1)
    if len(mentions) > 1:
        raise SearchParseError(
            "ambiguous_date",
            "Include exactly one departure date.",
        )
    mention = mentions[0]
    relative = _parse_relative_mention(mention, reference_date)
    if relative is not None:
        departure_date = relative
    else:
        default = datetime.combine(reference_date, datetime.min.time())
        try:
            parsed = date_parser.parse(
                mention,
                fuzzy=False,
                default=default,
                dayfirst=False,
            )
        except (ValueError, OverflowError, TypeError):
            raise SearchParseError(
                "invalid_date",
                "The departure date is not valid.",
            ) from None
        departure_date = parsed.date()

        if departure_date < reference_date and not _has_explicit_year(mention):
            try:
                departure_date = date(
                    reference_date.year + 1,
                    departure_date.month,
                    departure_date.day,
                )
            except ValueError:
                raise SearchParseError(
                    "invalid_date",
                    "The departure date is not valid.",
                ) from None
    if departure_date < reference_date:
        raise SearchParseError(
            "past_date",
            "Departure date cannot be in the past.",
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
        raise SearchParseError(
            "invalid_fare_paid", "Fare paid must be greater than zero."
        )
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


# --------------------------------------------------------------- flexible search

# IATA airline codes: two characters, letters or digits, never two digits
# (DL, AA, F9, B6, 9K).
_CARRIER = r"(?:[A-Za-z][A-Za-z0-9]|[0-9][A-Za-z])"
_FLEX_FLIGHT_PATTERNS = (
    # "flight DL 1234", "flight f9 2272"
    re.compile(
        rf"\bflight\s+(?P<carrier>{_CARRIER})\s*(?P<number>\d{{1,4}})\b", re.IGNORECASE
    ),
    # "DL1234", "dl1234", "F92272": no space, any case
    re.compile(rf"\b(?P<carrier>{_CARRIER})(?P<number>\d{{1,4}})\b", re.IGNORECASE),
    # "DL 1234", "F9 2272": with a space, uppercase only, so "to 3" isn't a flight
    re.compile(r"\b(?P<carrier>[A-Z][A-Z0-9]|[0-9][A-Z])\s+(?P<number>\d{1,4})\b"),
)

# Known airports only, so ordinary three-letter words ("the", "and") aren't
# mistaken for airport codes.
_KNOWN_PLACE = (
    "(?:"
    + "|".join(
        re.escape(alias) for alias in sorted(_AIRPORT_ALIASES, key=len, reverse=True)
    )
    + ")"
)
_FROM_PATTERN = re.compile(
    rf"\b(?:from|out of|leaving|departing)\s+(?P<place>{_KNOWN_PLACE})\b", re.IGNORECASE
)
_TO_PATTERN = re.compile(
    rf"\b(?:to|into|arriving(?:\s+in)?)\s+(?P<place>{_KNOWN_PLACE})\b", re.IGNORECASE
)
_ANY_PLACE_PATTERN = re.compile(rf"\b(?P<place>{_KNOWN_PLACE})\b", re.IGNORECASE)


@dataclass(frozen=True)
class FlightSearchIntent:
    """What a search box query asked for. Any part may be missing."""

    origin: str | None
    destination: str | None
    # A place given without "from" or "to": the route can go either way.
    either_airport: str | None
    # Normalized like "DL 1234".
    flight_number: str | None
    departure_date: date


def _airport(text: str) -> str:
    return _AIRPORT_ALIASES.get(text.lower(), text.upper())


def _parse_flex_flight_number(query: str) -> str | None:
    matches = {
        f"{match.group('carrier').upper()} {match.group('number')}"
        for pattern in _FLEX_FLIGHT_PATTERNS
        for match in pattern.finditer(query)
    }
    if len(matches) > 1:
        raise SearchParseError(
            "ambiguous_flight_number", "Include at most one flight number."
        )
    return next(iter(matches)) if matches else None


def parse_flight_search(
    query: str, *, reference_date: date | None = None
) -> FlightSearchIntent:
    """Read whatever the query gives: a route, one airport, a flight number, a date."""

    today = reference_date or date.today()
    departure_date = _parse_departure_date(query, today)
    flight_number = _parse_flex_flight_number(query)

    origin = destination = either = None
    try:
        origin, destination = _parse_route(query)
    except SearchParseError as error:
        if error.code == "ambiguous_route":
            raise
        if match := _FROM_PATTERN.search(query):
            origin = _airport(match.group("place"))
        if match := _TO_PATTERN.search(query):
            destination = _airport(match.group("place"))
        if origin is None and destination is None:
            places = {
                _airport(m.group("place")) for m in _ANY_PLACE_PATTERN.finditer(query)
            }
            if len(places) > 1:
                raise SearchParseError(
                    "ambiguous_route",
                    'Say which way you\'re flying, such as "Atlanta to Miami".',
                ) from None
            either = next(iter(places), None)

    if not (origin or destination or either or flight_number):
        raise SearchParseError(
            "missing_search",
            'Search by route, airport or flight, such as "Atlanta to Miami", '
            '"from Atlanta tomorrow" or "DL 1234".',
        )
    return FlightSearchIntent(
        origin=origin,
        destination=destination,
        either_airport=either,
        flight_number=flight_number,
        departure_date=departure_date,
    )
