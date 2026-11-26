"""Display helpers for rendering domain values in the Streamlit pages.

All user-facing text comes from the translation catalogs. These helpers keep
formatting and key lookups out of the page code.
"""

from __future__ import annotations

from fiskal.db.models import Category, CostCenter
from fiskal.i18n import translate


def category_label(category: Category | None) -> str:
    """Return the display name for a category."""
    if category is None:
        return ""
    if category.custom_name:
        return category.custom_name
    if category.name_key:
        return translate(category.name_key)
    return ""


def cost_center_label(cost_center: CostCenter | None) -> str:
    """Return the display name for a cost center."""
    if cost_center is None:
        return ""
    if cost_center.custom_name:
        return cost_center.custom_name
    if cost_center.name_key:
        return translate(cost_center.name_key)
    return ""


def transaction_type_label(value: str | None) -> str:
    """Return the display name for a transaction type."""
    if value is None:
        return ""
    return translate(f"type.{value}")


def status_label(value: str | None) -> str:
    """Return the display name for a transaction status."""
    if value is None:
        return ""
    return translate(f"status.{value}")


def person_label(name: str | None) -> str:
    """Return the display name for a person, or the unattributed label."""
    if name is None:
        return translate("person.unattributed")
    return name


def format_minor(amount_minor: int, currency_code: str) -> str:
    """Format integer minor units as a plain decimal with its currency code."""
    return f"{amount_minor / 100:.2f} {currency_code}"
