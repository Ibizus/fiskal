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


class RuleMatchMode(StrEnum):
    """How a rule matches its text condition."""

    CONTAINS = "contains"
    REGEX = "regex"


class RuleOrigin(StrEnum):
    """Where a classification rule came from."""

    SEED = "seed"
    USER = "user"


class IncomeSource(StrEnum):
    """The source of an income category for reporting.

    Earned income is work plus benefits plus rental. ``other`` marks gifts and
    one-off inflows, which count in the cash balance but not in earned income.
    """

    WORK = "work"
    BENEFIT = "benefit"
    RENTAL = "rental"
    OTHER = "other"


class GroupDirection(StrEnum):
    """Which side of the books a guided-mode group of movements is on.

    Income and expense groups stay apart even when their descriptions match:
    a refund from a shop is not the same concept as a purchase there.
    """

    INCOME = "income"
    EXPENSE = "expense"
