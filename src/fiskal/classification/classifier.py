"""Extension point for optional classifiers (for example an AI layer in v2).

The classification pipeline documents an optional AI layer between history and
the account default cost center. A ``Classifier`` implementation returns a
suggestion with a confidence level and never validates by itself. No AI
classifier is implemented in v1; this module only defines the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from fiskal.domain.enums import TransactionType


@dataclass(frozen=True)
class ClassificationSuggestion:
    """A proposed classification with a confidence level in ``0.0..1.0``."""

    transaction_type: TransactionType
    category_id: UUID
    cost_center_id: UUID | None
    confidence: float


class Classifier(Protocol):
    """Suggests a classification for one transaction, or declines with None.

    Only normalized concepts are exposed (normalized description, counterparty,
    amount and operation date); raw bank text never leaves the boundary.
    """

    def suggest(
        self,
        *,
        normalized_description: str,
        counterparty: str | None,
        amount_minor: int,
        operation_date: date,
    ) -> ClassificationSuggestion | None:
        """Return a suggestion with a confidence level, or None to decline."""
        ...
