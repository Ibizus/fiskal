"""Neutral default seed data for the domain.

All display text is referenced by translation keys. The actual translations
live in ``src/fiskal/i18n/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Category:
    """A spending or income category.

    Attributes:
        key: Translation key used to look up the display name.
        kind: Either ``"income"`` or ``"expense"``.
    """

    key: str
    kind: str


@dataclass(frozen=True)
class CostCenter:
    """A cost-center grouping.

    Attributes:
        key: Translation key used to look up the display name.
    """

    key: str


DEFAULT_CATEGORIES: Final[tuple[Category, ...]] = (
    Category("category.income", "income"),
    Category("category.housing", "expense"),
    Category("category.utilities", "expense"),
    Category("category.food", "expense"),
    Category("category.transport", "expense"),
    Category("category.healthcare", "expense"),
    Category("category.education", "expense"),
    Category("category.entertainment", "expense"),
    Category("category.shopping", "expense"),
    Category("category.savings", "expense"),
    Category("category.taxes", "expense"),
    Category("category.misc", "expense"),
)

DEFAULT_COST_CENTERS: Final[tuple[CostCenter, ...]] = (
    CostCenter("costcenter.personal"),
    CostCenter("costcenter.household"),
    CostCenter("costcenter.work"),
)
