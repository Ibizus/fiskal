"""Tests for the form parsing helpers."""

from __future__ import annotations

import pytest

from fiskal.ui.forms import FormValueError, parse_optional_amount_minor

MINOR_TWELVE_THIRTY_FOUR = 1234
MINOR_TWELVE_THIRTY = 1230


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
