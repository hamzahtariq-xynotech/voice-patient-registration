"""Reusable validation helpers.

These are plain functions (not Pydantic-specific) so the voice tool handler and
the REST API enforce exactly the same rules. Each raises ValueError with a
message written in plain English -- the voice agent reads these aloud.

Two principles keep the agent out of retry loops:

1. Normalize rather than reject. Speech-to-text returns "787 01", "78701.",
   "seven eight seven zero one" and "T X" for values a caller said perfectly
   well. Anything we can read unambiguously, we accept.
2. Every error says what to do next, not just what is wrong. The message goes
   straight back to the model, so "ask the caller to say it one digit at a time"
   changes its next turn, where "invalid zip" just makes it ask the same way.
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
    "district of columbia": "DC", "washington dc": "DC", "washington d c": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
_SEX_ALIASES = {
    "m": "Male", "male": "Male", "man": "Male",
    "f": "Female", "female": "Female", "woman": "Female",
    "o": "Other", "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline": "Decline to Answer", "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "n/a": "Decline to Answer",
}

# Spoken digits. "oh" and "o" are how callers usually say a zero in a ZIP or a
# phone number; "double"/"triple" repeat whichever digit follows.
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

# Callers group digits when reading a number aloud: "five one two, five fifty
# five, oh one thirty four". Teens are one token; tens absorb a following unit
# ("fifty five" is 55, not 50 then 5), so they are handled separately.
_TEEN_WORDS = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19",
}
_TENS_WORDS = {
    "twenty": "2", "thirty": "3", "forty": "4", "fourty": "4", "fifty": "5",
    "sixty": "6", "seventy": "7", "eighty": "8", "ninety": "9",
}

# Words a caller may wrap around a number without changing it. Anything outside
# this set makes the value ambiguous, and ambiguous means reject -- see
# extract_digits.
_FILLER_WORDS = {
    "my", "the", "is", "it", "its", "s", "a", "and", "please", "sure", "ok",
    "okay", "yes", "that", "thats", "zip", "zipcode", "postal", "code", "number",
    "phone", "cell", "mobile", "area", "plus", "dash", "hyphen",
}

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' -]*$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
MIN_DOB = date(1900, 1, 1)


def extract_digits(value) -> str | None:
    """Read an ordered digit string out of what speech-to-text produced.

    Handles literal digits, separators between them, spelled-out number words,
    "double seven", and the filler a caller wraps around a number ("my zip
    code is ...").

    Returns None when the text contains a word we cannot account for. That
    matters more than it looks: harvesting stray digits out of free text turns
    "1425 Oak Street apt 5" into the ZIP 14255 and stores an invented value as
    fact. Rejecting instead sends VALIDATION_ERROR back to the model, which
    re-asks the caller -- a wasted turn is always cheaper than wrong data in a
    patient record.

    For ZIP and phone fields only -- never run this over a name.
    """
    tokens = re.findall(r"[a-z]+|\d", str(value or "").lower())
    out: list[str] = []
    repeat = 1
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.isdigit():
            out.extend([token] * repeat)
            repeat = 1
        elif token in ("double", "triple"):
            repeat = 2 if token == "double" else 3
            i += 1
            continue
        elif token in _DIGIT_WORDS:
            out.extend([_DIGIT_WORDS[token]] * repeat)
            repeat = 1
        elif token in _TEEN_WORDS:
            out.append(_TEEN_WORDS[token])
            repeat = 1
        elif token in _TENS_WORDS:
            # "fifty five" is 55; a bare "fifty" is 50.
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt in _DIGIT_WORDS and _DIGIT_WORDS[nxt] != "0":
                out.append(_TENS_WORDS[token] + _DIGIT_WORDS[nxt])
                i += 2
                repeat = 1
                continue
            out.append(_TENS_WORDS[token] + "0")
            repeat = 1
        elif token in _FILLER_WORDS:
            repeat = 1
        else:
            return None  # a word we cannot interpret: refuse to guess
        i += 1
    return "".join(out)


def _spoken(field: str) -> str:
    return field.replace("_", " ")


def validate_name(value: str, field: str = "name") -> str:
    value = (value or "").strip().strip(".,")
    if not 1 <= len(value) <= 50:
        raise ValueError(
            f"{field} must be between 1 and 50 characters. Ask the caller to say "
            f"their {_spoken(field)} again"
        )
    if not NAME_RE.match(value):
        raise ValueError(
            f"{field} may only contain letters, spaces, hyphens and apostrophes. "
            f"Ask the caller to spell their {_spoken(field)} letter by letter"
        )
    return value


def normalize_phone(value, field: str = "phone_number") -> str:
    """Strip formatting, drop a leading US country code, require 10 digits."""
    raw = str(value or "").strip()
    digits = extract_digits(raw)
    if digits is None:
        raise ValueError(
            f"{raw!r} does not look like a {_spoken(field)}. Ask the caller for "
            "the 10 digit number and send only the digits"
        )
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            f"could not read a 10 digit {_spoken(field)} from {raw!r} "
            f"(found {len(digits)} digits). Ask the caller to say the number "
            "one digit at a time"
        )
    if digits[0] not in "23456789":
        raise ValueError(
            f"{field} area code cannot start with 0 or 1. Ask the caller to "
            "confirm the first three digits"
        )
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
            raise ValueError(
                "date_of_birth is required. Ask the caller for the month, day and year"
            )
        cleaned = raw.rstrip(".").replace(".", "/")
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y",
                    "%B %d %Y", "%b %d %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(cleaned, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                f"could not read a date from {raw!r}. Ask the caller for the "
                "month, day and year separately, and send it as MM/DD/YYYY"
            )
    if parsed > date.today():
        raise ValueError(
            "date_of_birth cannot be in the future. Tell the caller that date is "
            "in the future and ask for their year of birth again"
        )
    if parsed < MIN_DOB:
        raise ValueError(
            "date_of_birth must be after the year 1900. Ask the caller to "
            "confirm their year of birth"
        )
    return parsed


def format_dob(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def normalize_sex(value) -> str:
    raw = str(value or "").strip().strip(".,")
    if not raw:
        raise ValueError(
            "sex is required. Ask the caller whether to record male, female, "
            "other, or decline to answer"
        )
    key = raw.lower()
    if key in _SEX_ALIASES:
        return _SEX_ALIASES[key]
    for canonical in SEX_VALUES:
        if canonical.lower() == key:
            return canonical
    raise ValueError(
        f"did not recognise {raw!r} as a sex. Ask the caller to choose male, "
        "female, other, or decline to answer"
    )


def normalize_state(value) -> str:
    """Accept a two-letter code or a spoken state name, however punctuated."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("state is required. Ask the caller which state they live in")
    # Drop punctuation ("texas.", "D.C.") and collapse runs of whitespace.
    cleaned = re.sub(r"[^A-Za-z ]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^(the )?state of ", "", cleaned, flags=re.I).strip()
    compact = cleaned.replace(" ", "")  # "T X" -> "TX", "tex as" -> "texas"

    if len(compact) == 2 and compact.upper() in US_STATES:
        return compact.upper()
    full = STATE_NAMES.get(cleaned.lower()) or STATE_NAMES.get(compact.lower())
    if full:
        return full
    raise ValueError(
        f"did not recognise {raw!r} as a US state. Ask the caller to say the "
        "full state name, for example Texas"
    )


def validate_zip(value) -> str:
    """Accept whatever number the caller gives as a postal code.

    Deliberately permissive on length. Demanding exactly 5 digits was the main
    source of the agent looping: a caller says a ZIP, the transcriber drops or
    adds a digit, the tool rejects, and the agent asks again with no better
    result. A short postal code stored as given is a small, visible data-quality
    problem; a call that never completes loses the whole registration.

    The one thing still refused is a value with no number in it at all -- that
    means the model sent the wrong field, and re-asking genuinely helps. A ZIP+4
    is hyphenated for tidiness; everything else is stored as the digits given.
    """
    raw = str(value or "").strip()
    if ZIP_RE.match(raw):
        return raw

    digits = extract_digits(raw)
    if digits is None:
        # Free text with a number in it ("78701 Austin"): take the longest run
        # of digits rather than stitching scattered ones into an invented code.
        runs = re.findall(r"\d+", raw)
        digits = max(runs, key=len) if runs else ""

    if not digits:
        raise ValueError(
            f"{raw!r} does not contain a postal code. Ask the caller for their "
            "ZIP code and send only the digits"
        )
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    return digits[:10]  # column is String(10)


def validate_city(value) -> str:
    raw = str(value or "").strip()
    # Callers routinely answer "Austin, Texas" -- keep the city half.
    raw = re.sub(r"\s*,.*$", "", raw).strip(" .,")
    if not 1 <= len(raw) <= 100:
        raise ValueError("city is required. Ask the caller which city they live in")
    return raw


def normalize_language(value) -> str:
    raw = str(value or "").strip() if isinstance(value, str) else ""
    return raw or "English"
