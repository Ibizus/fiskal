"""Tests for the form parsing helpers."""

from __future__ import annotations

import pytest

from fiskal.ui.forms import (
    MAX_STORED_INT,
    FormValueError,
    parse_optional_amount_minor,
    parse_optional_int,
)

MINOR_TWELVE_THIRTY_FOUR = 1234
MINOR_TWELVE_THIRTY = 1230
LARGEST_STORABLE_AMOUNT = f"{MAX_STORED_INT // 100}.{MAX_STORED_INT % 100:02d}"
SMALLEST_UNSTORABLE_AMOUNT = f"{MAX_STORED_INT // 100}.{MAX_STORED_INT % 100 + 1:02d}"


def test_amount_parses_comma_and_point() -> None:
    """Both decimal separators and trailing zeros are accepted."""
    assert parse_optional_amount_minor("12,34") == MINOR_TWELVE_THIRTY_FOUR
    assert parse_optional_amount_minor(" 12.34 ") == MINOR_TWELVE_THIRTY_FOUR
    assert parse_optional_amount_minor("12.340") == MINOR_TWELVE_THIRTY_FOUR
    assert parse_optional_amount_minor("12.3") == MINOR_TWELVE_THIRTY
    assert parse_optional_amount_minor("") is None


@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "-Infinity", "snan", "abc"])
def test_amount_rejects_non_numbers(text: str) -> None:
    """Text and special values such as NaN or infinity are refused."""
    with pytest.raises(FormValueError):
        parse_optional_amount_minor(text)


@pytest.mark.parametrize("text", ["12.345", "0.001", "1,999"])
def test_amount_rejects_sub_cent_precision(text: str) -> None:
    """Amounts finer than one cent are refused instead of truncated."""
    with pytest.raises(FormValueError):
        parse_optional_amount_minor(text)


@pytest.mark.parametrize(
    "text", ["1e20", "-1e20", "1e999999", SMALLEST_UNSTORABLE_AMOUNT]
)
def test_amount_rejects_values_too_large_to_store(text: str) -> None:
    """Amounts beyond the stored integer range are refused."""
    with pytest.raises(FormValueError):
        parse_optional_amount_minor(text)


def test_amount_accepts_largest_storable_value() -> None:
    """The largest amount that fits the stored integer is still accepted."""
    assert parse_optional_amount_minor(LARGEST_STORABLE_AMOUNT) == MAX_STORED_INT


def test_int_rejects_values_too_large_to_store() -> None:
    """Whole numbers beyond the stored integer range are refused."""
    assert parse_optional_int(str(MAX_STORED_INT)) == MAX_STORED_INT
    with pytest.raises(FormValueError):
        parse_optional_int(str(MAX_STORED_INT + 1))
