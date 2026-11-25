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
        nature: Either ``"fixed"`` or ``"variable"`` for reporting.
    """

    key: str
    kind: str
    nature: str


@dataclass(frozen=True)
class CostCenter:
    """A cost-center grouping.

    Attributes:
        key: Translation key used to look up the display name.
    """

    key: str


@dataclass(frozen=True)
class SeedRule:
    """A neutral, non-personal default classification rule.

    Attributes:
        priority: Numeric precedence inside the seed group (lower wins).
        mode: ``"contains"`` (the default matching mode).
        pattern: The text matched case-insensitively against normalized fields.
        transaction_type: The type the rule assigns.
        category_key: Translation key of the category the rule assigns.
        cost_center_key: Optional translation key of the cost center, or
            ``None`` to leave the cost center to the account default.
    """

    priority: int
    mode: str
    pattern: str
    transaction_type: str
    category_key: str
    cost_center_key: str | None


DEFAULT_CATEGORIES: Final[tuple[Category, ...]] = (
    Category("category.income", "income", "variable"),
    Category("category.housing", "expense", "fixed"),
    Category("category.utilities", "expense", "variable"),
    Category("category.food", "expense", "variable"),
    Category("category.transport", "expense", "variable"),
    Category("category.healthcare", "expense", "variable"),
    Category("category.education", "expense", "variable"),
    Category("category.entertainment", "expense", "variable"),
    Category("category.shopping", "expense", "variable"),
    Category("category.savings", "expense", "fixed"),
    Category("category.taxes", "expense", "variable"),
    Category("category.misc", "expense", "variable"),
)

DEFAULT_COST_CENTERS: Final[tuple[CostCenter, ...]] = (
    CostCenter("costcenter.personal"),
    CostCenter("costcenter.household"),
    CostCenter("costcenter.work"),
)

# The seed rules are deliberately tiny and generic: no merchants, no bank- or
# country-specific text, and no personal data. Real household rules come from
# the validation queue. Each rule leaves the cost center unset so the account's
# default cost center applies as the last classification layer.
DEFAULT_RULES: Final[tuple[SeedRule, ...]] = (
    SeedRule(
        priority=100,
        mode="contains",
        pattern="salary",
        transaction_type="income",
        category_key="category.income",
        cost_center_key=None,
    ),
    SeedRule(
        priority=90,
        mode="contains",
        pattern="payroll",
        transaction_type="income",
        category_key="category.income",
        cost_center_key=None,
    ),
    SeedRule(
        priority=80,
        mode="contains",
        pattern="cash withdrawal",
        transaction_type="expense",
        category_key="category.misc",
        cost_center_key=None,
    ),
)
