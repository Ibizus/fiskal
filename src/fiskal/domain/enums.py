"""Framework-free enumerations for the Fiskal domain.

These value objects live in ``domain`` so every other package can depend on
 them without pulling in persistence or UI code.
"""

from __future__ import annotations

from enum import StrEnum


class TransactionType(StrEnum):
    """The effect of a transaction on the books."""

    EXPENSE = "expense"
    INCOME = "income"
    SAVINGS_INVESTMENT = "savings_investment"
    INTERNAL_TRANSFER = "internal_transfer"
    REFUND = "refund"


class TransactionStatus(StrEnum):
    """Workflow state of a transaction after import or correction."""

    PENDING = "pending"
    VALIDATED = "validated"


class CategoryKind(StrEnum):
    """Whether a category represents money coming in or going out."""

    INCOME = "income"
    EXPENSE = "expense"


class CategoryNature(StrEnum):
    """Stability of an expense category for reporting."""

    FIXED = "fixed"
    VARIABLE = "variable"


class ClassificationOrigin(StrEnum):
    """Source of a transaction's classification."""

    RULE = "rule"
    HISTORY = "history"
    MANUAL = "manual"
    TEMPLATE = "template"
