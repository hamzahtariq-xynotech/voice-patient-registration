"""Reusable validation helpers.

These are plain functions (not Pydantic-specific) so the voice tool handler and
the REST API enforce exactly the same rules. Each raises ValueError with a
message written in plain English -- the voice agent reads these aloud.
"""

import re
from datetime import date, datetime

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "washington dc": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
_SEX_ALIASES = {
    "m": "Male", "male": "Male", "man": "Male",
    "f": "Female", "female": "Female", "woman": "Female",
    "o": "Other", "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline": "Decline to Answer", "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "n/a": "Decline to Answer",
}

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' -]*$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
MIN_DOB = date(1900, 1, 1)


def validate_name(value: str, field: str = "name") -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= 50:
        raise ValueError(f"{field} must be between 1 and 50 characters")
    if not NAME_RE.match(value):
        raise ValueError(
            f"{field} may only contain letters, spaces, hyphens and apostrophes"
        )
    return value


def normalize_phone(value: str, field: str = "phone_number") -> str:
    """Strip formatting, drop a leading US country code, require 10 digits (NANP)."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"{field} must be exactly 10 digits")
    if digits[0] not in "23456789":
        raise ValueError(f"{field} area code cannot start with 0 or 1")
    return digits


def parse_dob(value) -> date:
    """Accept MM/DD/YYYY, ISO YYYY-MM-DD or a date object. Reject impossible dates."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("date_of_birth is required")
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError("date_of_birth must look like MM/DD/YYYY")
    if parsed > date.today():
        raise ValueError("date_of_birth cannot be in the future")
    if parsed < MIN_DOB:
        raise ValueError("date_of_birth must be after the year 1900")
    return parsed


def format_dob(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def normalize_sex(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("sex is required")
    key = raw.lower()
    if key in _SEX_ALIASES:
        return _SEX_ALIASES[key]
    for canonical in SEX_VALUES:
        if canonical.lower() == key:
            return canonical
    raise ValueError("sex must be Male, Female, Other, or Decline to Answer")


def normalize_state(value: str) -> str:
    """Accept either a two-letter code or a spoken full state name."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("state is required")
    if len(raw) == 2 and raw.upper() in US_STATES:
        return raw.upper()
    full = STATE_NAMES.get(raw.lower())
    if full:
        return full
    raise ValueError("state must be a valid US state abbreviation, for example CA")


def validate_zip(value: str) -> str:
    raw = (value or "").strip()
    if not ZIP_RE.match(raw):
        raise ValueError("zip_code must be 5 digits, or 5 digits plus a 4 digit extension")
    return raw


def validate_city(value: str) -> str:
    raw = (value or "").strip()
    if not 1 <= len(raw) <= 100:
        raise ValueError("city must be between 1 and 100 characters")
    return raw


def normalize_language(value) -> str:
    raw = (value or "").strip() if isinstance(value, str) else ""
    return raw or "English"
