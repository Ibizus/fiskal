"""Parsing helpers for optional form inputs.

Pages collect text; these helpers turn it into the typed values the services
expect, so the pages hold no conversion logic.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

MINOR_UNITS_PER_MAJOR = 100
MAX_STORED_INT = 2**63 - 1


class FormValueError(ValueError):
    """Raised when a form field cannot be read."""


def parse_optional_amount_minor(text: str) -> int | None:
    """Read an optional decimal amount and return integer minor units."""
    cleaned = text.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise FormValueError(text) from exc
    if (
        not amount.is_finite()
        or abs(amount) > Decimal(MAX_STORED_INT) / MINOR_UNITS_PER_MAJOR
    ):
        raise FormValueError(text)
    minor = amount * MINOR_UNITS_PER_MAJOR
    if minor != minor.to_integral_value():
        raise FormValueError(text)
    return int(minor)


def parse_optional_int(text: str) -> int | None:
    """Read an optional whole number."""
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        value = int(cleaned)
    except ValueError as exc:
        raise FormValueError(text) from exc
    if abs(value) > MAX_STORED_INT:
        raise FormValueError(text)
    return value


def format_optional_amount(amount_minor: int | None) -> str:
    """Render integer minor units as the decimal text a form field shows."""
    if amount_minor is None:
        return ""
    return f"{amount_minor / MINOR_UNITS_PER_MAJOR:.2f}"


def format_optional_int(value: int | None) -> str:
    """Render an optional whole number as form text."""
    return "" if value is None else str(value)
