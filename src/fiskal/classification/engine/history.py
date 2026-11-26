"""Validated-history suggestions and the history classification layer."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)


@dataclass(frozen=True)
class HistoryOutcome:
    """One validated classification seen for a counterparty, with its count."""

    transaction_type: TransactionType
    cost_center_id: UUID | None
    category_id: UUID
    count: int


@dataclass(frozen=True)
class HistorySuggestion:
    """Validated history for a pending transaction's counterparty.

    ``preselected`` is the most frequent validated outcome; ``alternatives``
    holds the other outcomes, most frequent first. The validation queue
    recomputes this when it opens, so nothing is stored.
    """

    transaction_id: UUID
    counterparty: str
    preselected: HistoryOutcome
    alternatives: tuple[HistoryOutcome, ...]


def suggest_from_history(
    session: Session, transaction_id: UUID
) -> HistorySuggestion | None:
    """Return the validated history suggestion for one pending transaction.

    Returns None when the transaction does not exist, is not pending, is
    paired, has no counterparty, or its counterparty has no validated history.
    """
    txn = session.get(Transaction, transaction_id)
    if (
        txn is None
        or txn.status != TransactionStatus.PENDING.value
        or txn.paired_transaction_id is not None
        or txn.counterparty is None
    ):
        return None
    outcomes = _validated_outcomes(session, {txn.counterparty}).get(txn.counterparty)
    if not outcomes:
        return None
    ranked = _ranked_outcomes(outcomes)
    return HistorySuggestion(
        transaction_id=txn.id,
        counterparty=txn.counterparty,
        preselected=ranked[0],
        alternatives=tuple(ranked[1:]),
    )


_Outcome = tuple[str, UUID | None, UUID]


def _validated_outcomes(
    session: Session, counterparties: set[str]
) -> dict[str, Counter[_Outcome]]:
    """Count validated (type, cost center, category) outcomes per counterparty."""
    validated = list(
        session.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.category_id.is_not(None),
                Transaction.counterparty.in_(counterparties),
            )
        ).scalars()
    )

    history: dict[str, Counter[_Outcome]] = {}
    for row in validated:
        counterparty = row.counterparty
        if counterparty is None or row.category_id is None:
            continue
        history.setdefault(counterparty, Counter())[
            (row.transaction_type, row.cost_center_id, row.category_id)
        ] += 1
    return history


def _ranked_outcomes(outcomes: Counter[_Outcome]) -> list[HistoryOutcome]:
    """Order outcomes by descending count with a deterministic tie-break."""
    ordered = sorted(
        outcomes.items(),
        key=lambda item: (-item[1], item[0][0], str(item[0][1]), str(item[0][2])),
    )
    return [
        HistoryOutcome(
            transaction_type=TransactionType(outcome_type),
            cost_center_id=cost_center_id,
            category_id=category_id,
            count=count,
        )
        for (outcome_type, cost_center_id, category_id), count in ordered
    ]


def apply_history(session: Session, targets: list[Transaction]) -> int:
    """Classify from validated history when a counterparty is unambiguous.

    A counterparty with a single validated outcome is applied (origin history).
    A counterparty with several outcomes stays unclassified; the validation
    queue gets its preselection from ``suggest_from_history``.
    """
    counterparts = {txn.counterparty for txn in targets if txn.counterparty}
    if not counterparts:
        return 0

    history = _validated_outcomes(session, counterparts)
    count = 0
    for txn in targets:
        if txn.counterparty is None:
            continue
        outcomes = history.get(txn.counterparty)
        if not outcomes:
            continue
        if len(outcomes) == 1:
            ((outcome_type, cost_center_id, category_id), _) = next(
                iter(outcomes.items())
            )
            txn.transaction_type = outcome_type
            txn.cost_center_id = cost_center_id
            txn.category_id = category_id
            txn.origin = ClassificationOrigin.HISTORY.value
            txn.rule_id = None
            count += 1
    return count
