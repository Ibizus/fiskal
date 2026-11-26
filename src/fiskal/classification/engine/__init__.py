"""Classification engine: the deterministic cascade for pending transactions.

The layers run in the documented order: deterministic rules, then history, then
the account's default cost center. The engine only writes to pending, unpaired
transactions and never touches rows that are already validated. A match by a
rule the owner created also validates the row (``validated_by_rule``);
seed rules and history only preselect, so those rows stay in the queue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from fiskal.classification.engine.default_cost_center import apply_default_cost_center
from fiskal.classification.engine.history import (
    HistoryOutcome,
    HistorySuggestion,
    apply_history,
    suggest_from_history,
)
from fiskal.classification.engine.reapply import (
    ReapplyChange,
    apply_reapply_validated,
    preview_reapply_validated,
)
from fiskal.classification.engine.rule_cascade import (
    apply_rules,
    rule_to_condition,
    sorted_active_rules,
)
from fiskal.classification.pairing_service import find_proposals_for_transaction_ids
from fiskal.db.models import Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)

_ORIGIN_PLACEHOLDER = ClassificationOrigin.MANUAL.value


def _default_type(amount_minor: int) -> TransactionType:
    return TransactionType.INCOME if amount_minor >= 0 else TransactionType.EXPENSE


@dataclass(frozen=True)
class ClassificationResult:
    """Counts for one classification run over a batch of pending transactions.

    ``classified_by_rule`` and ``classified_by_history`` count transactions that
    received a category from that layer. ``validated_by_rule`` counts the subset
    of ``classified_by_rule`` that a user rule also validated.
    ``classified_by_default`` counts transactions that received only their
    cost center from the account default (they stay unclassified and in the
    queue). ``unclassified`` counts transactions that still have no category
    after all layers.
    """

    classified_by_rule: int = 0
    validated_by_rule: int = 0
    classified_by_history: int = 0
    classified_by_default: int = 0
    unclassified: int = 0


def classify_pending(
    session: Session,
    transaction_ids: Sequence[UUID] | None = None,
) -> ClassificationResult:
    """Classify pending, unclassified, unpaired transactions through the cascade.

    When ``transaction_ids`` is given, only those transactions are considered;
    otherwise every eligible transaction in the profile is classified. The
    engine validates a transaction only when a rule created by the owner
    matches it, and never touches transactions that are already validated.
    Ambiguous history is not returned here; the validation queue asks
    ``suggest_from_history`` for it.
    """
    return _classify(session, transaction_ids, previous_rule_ids={})


def _classify(
    session: Session,
    transaction_ids: Sequence[UUID] | None,
    *,
    previous_rule_ids: Mapping[UUID, UUID | None],
) -> ClassificationResult:
    targets = _load_unclassified(session, transaction_ids)
    if not targets:
        return ClassificationResult()

    proposals = find_proposals_for_transaction_ids(session, [t.id for t in targets])
    held_ids = {p.source_id for p in proposals} | {p.candidate_id for p in proposals}
    classified_by_rule = apply_rules(session, targets, previous_rule_ids, held_ids)
    validated_by_rule = sum(
        1 for txn in targets if txn.status == TransactionStatus.VALIDATED.value
    )
    remaining = [txn for txn in targets if txn.category_id is None]
    classified_by_history = apply_history(session, remaining)
    classified_by_default = apply_default_cost_center(session, targets)
    unclassified = sum(1 for txn in targets if txn.category_id is None)

    session.commit()
    return ClassificationResult(
        classified_by_rule=classified_by_rule,
        validated_by_rule=validated_by_rule,
        classified_by_history=classified_by_history,
        classified_by_default=classified_by_default,
        unclassified=unclassified,
    )


def reclassify_pending(
    session: Session,
    transaction_ids: Sequence[UUID] | None = None,
) -> ClassificationResult:
    """Reset engine-derived classifications, then reclassify.

    Only unpaired transactions whose classification came from the engine
    (rules or history) or that are still unclassified are reset. That includes
    rows a user rule validated automatically (``validated_by_rule``): they
    return to pending and are validated again only if a user rule still matches
    them, so editing or deleting a rule keeps its rows consistent. A pending
    row the user already classified by hand (``origin`` manual or template with
    a category set) is left untouched, as are rows the user validated by hand.
    The person on a reset row whose ``origin`` is rule is owned by the engine
    and is cleared with the rest of the rule's classification. A hand-set
    attribution is protected because a manual edit switches the row's
    ``origin`` to manual, and classified manual rows are never reset.
    A rule's hit is only counted when a row moves to a different rule.
    """
    targets = _load_reclassify_targets(session, transaction_ids)
    previous_rule_ids = {txn.id: txn.rule_id for txn in targets}
    for txn in targets:
        if txn.origin == ClassificationOrigin.RULE.value:
            txn.person_id = None
        txn.transaction_type = _default_type(txn.amount_minor).value
        txn.category_id = None
        txn.cost_center_id = None
        txn.origin = _ORIGIN_PLACEHOLDER
        txn.rule_id = None
        txn.status = TransactionStatus.PENDING.value
        txn.validated_by_rule = False
    return _classify(session, transaction_ids, previous_rule_ids=previous_rule_ids)


def _load_unclassified(
    session: Session, transaction_ids: Sequence[UUID] | None
) -> list[Transaction]:
    statement = select(Transaction).where(
        Transaction.status == TransactionStatus.PENDING.value,
        Transaction.category_id.is_(None),
        Transaction.paired_transaction_id.is_(None),
    )
    if transaction_ids is not None:
        statement = statement.where(Transaction.id.in_(transaction_ids))
    return list(session.execute(statement).scalars())


def _load_reclassify_targets(
    session: Session, transaction_ids: Sequence[UUID] | None
) -> list[Transaction]:
    rule_origin = ClassificationOrigin.RULE.value
    statement = select(Transaction).where(
        Transaction.paired_transaction_id.is_(None),
        or_(
            and_(
                Transaction.status == TransactionStatus.PENDING.value,
                or_(
                    Transaction.category_id.is_(None),
                    Transaction.origin.in_(
                        (rule_origin, ClassificationOrigin.HISTORY.value)
                    ),
                ),
            ),
            and_(
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.validated_by_rule.is_(True),
            ),
        ),
    )
    if transaction_ids is not None:
        statement = statement.where(Transaction.id.in_(transaction_ids))
    return list(session.execute(statement).scalars())


__all__ = [
    "ClassificationResult",
    "HistoryOutcome",
    "HistorySuggestion",
    "ReapplyChange",
    "apply_reapply_validated",
    "classify_pending",
    "preview_reapply_validated",
    "reclassify_pending",
    "rule_to_condition",
    "sorted_active_rules",
    "suggest_from_history",
]
