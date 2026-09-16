"""Validator tests, written around what speech-to-text actually produces.

Every input in the "accepts" lists was a real rejection at some point. A caller
saying their ZIP perfectly clearly can reach the API as "787 01", "78701." or
"seven eight seven zero one" depending on the transcriber, and rejecting those
put the agent in a re-ask loop.
"""

import pytest

from app.validators import (
    extract_digits,
    normalize_phone,
    normalize_state,
    parse_dob,
    validate_city,
    validate_zip,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("78701", "78701"),
        ("787 01", "78701"),
        ("7 8 7 0 1", "78701"),
        ("78701.", "78701"),
        ("seven eight seven zero one", "78701"),
        ("my zip code is seven eight seven oh one", "78701"),
        ("zip is 78701", "78701"),
        ("78701-1234", "78701-1234"),
        ("787011234", "78701-1234"),  # ZIP+4 without the hyphen
    ],
)
def test_zip_accepts_spoken_forms(raw, expected):
    assert validate_zip(raw) == expected


@pytest.mark.parametrize("raw,expected", [("123", "123"), ("1234567", "1234567"),
                                          ("7870", "7870"), ("787012", "787012")])
def test_zip_accepts_any_length(raw, expected):
    """Length is deliberately not enforced.

    Insisting on 5 digits was the main cause of the agent looping: the caller
    says a ZIP, the transcriber drops a digit, the tool rejects, and asking
    again produces the same transcription. Storing what was given keeps the
    registration alive and leaves a visible, fixable value in the record.
    """
    assert validate_zip(raw) == expected


def test_zip_takes_the_longest_digit_run_from_free_text():
    # "78701 Austin" should yield the ZIP, not a stitched-together number.
    assert validate_zip("78701 Austin") == "78701"


@pytest.mark.parametrize("raw", ["", "somewhere downtown", "not sure", "abcde"])
def test_zip_rejects_when_there_is_no_number(raw):
    """The one case still worth a retry: the model sent the wrong field."""
    with pytest.raises(ValueError) as exc:
        validate_zip(raw)
    assert "only the digits" in str(exc.value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("TX", "TX"),
        ("Texas", "TX"),
        ("texas.", "TX"),
        ("T X", "TX"),
        ("tex as", "TX"),
        (" Texas ", "TX"),
        ("state of Texas", "TX"),
        ("New York", "NY"),
        ("new  york", "NY"),
        ("NY.", "NY"),
        ("Washington D.C.", "DC"),
        ("District of Columbia", "DC"),
    ],
)
def test_state_accepts_spoken_forms(raw, expected):
    assert normalize_state(raw) == expected


def test_state_rejects_non_state_with_guidance():
    with pytest.raises(ValueError) as exc:
        normalize_state("Narnia")
    assert "full state name" in str(exc.value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(512) 555-0147", "5125550147"),
        ("+1 512 555 0147", "5125550147"),
        ("1-512-555-0147", "5125550147"),
        ("five one two five five five zero one four seven", "5125550147"),
        ("512 double five five 0147", "5125550147"),
        ("512 555 oh one four seven", "5125550147"),
    ],
)
def test_phone_accepts_spoken_forms(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["512-555-014", "555", ""])
def test_phone_rejects_wrong_length_with_guidance(raw):
    with pytest.raises(ValueError) as exc:
        normalize_phone(raw)
    assert "one digit at a time" in str(exc.value)


@pytest.mark.parametrize(
    "raw", ["not a number", "call me at 512 555 0147 after six", "my house is 1425"]
)
def test_phone_refuses_to_invent_from_free_text(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_phone_rejects_bad_area_code():
    with pytest.raises(ValueError) as exc:
        normalize_phone("1125550147")
    assert "first three digits" in str(exc.value)


@pytest.mark.parametrize(
    "raw,expected",
    [("Austin", "Austin"), ("Austin.", "Austin"), ("austin, texas", "austin"),
     ("  Austin  ", "Austin")],
)
def test_city_strips_noise(raw, expected):
    assert validate_city(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["03/05/1990", "1990-03-05", "March 05 1990", "03.05.1990", "3/5/90"],
)
def test_dob_accepts_several_formats(raw):
    assert parse_dob(raw).year == 1990


def test_dob_future_is_rejected_with_guidance():
    with pytest.raises(ValueError) as exc:
        parse_dob("01/01/2099")
    assert "future" in str(exc.value)


def test_dob_unreadable_tells_model_what_to_ask():
    with pytest.raises(ValueError) as exc:
        parse_dob("sometime in the nineties")
    assert "month, day and year" in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        "five one two five five five zero one three four",
        "five one two triple five zero one three four",
        "five one two double five five zero one three four",
        "five one two five fifty five oh one thirty four",
        "512 triple five 0134",
    ],
)
def test_phone_accepts_grouped_speech(raw):
    """Callers group digits aloud, and the transcriber passes the grouping through.

    An agent that had to count these itself insisted a correct ten-digit number
    was nine, three times in a row, so the counting lives here where it is exact.
    """
    assert normalize_phone(raw) == "5125550134"


@pytest.mark.parametrize(
    "raw,expected",
    [("five fifty five", "555"), ("zero one thirty four", "0134"),
     ("twenty one", "21"), ("fifty", "50"), ("nineteen ninety", "1990")],
)
def test_tens_absorb_a_following_unit(raw, expected):
    # "fifty five" is 55, not 50 followed by 5.
    assert extract_digits(raw) == expected


def test_extract_digits_ignores_known_filler():
    assert extract_digits("my number is five five five") == "555"
    assert extract_digits("") == ""


def test_extract_digits_returns_none_on_unknown_words():
    """None is the signal to reject rather than guess."""
    assert extract_digits("no digits here") is None
    assert extract_digits("1425 Oak Street") is None
