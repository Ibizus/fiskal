"""Classification engine: the deterministic cascade for pending transactions.

The layers run in the documented order: deterministic rules, then history, then
the account's default cost center. The engine only writes to pending, unpaired
transactions and never touches validated rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from fiskal.db.models import Account, Rule, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    RuleMatchMode,
    RuleOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.rule_matching import RuleCondition, condition_matches

_ORIGIN_PLACEHOLDER = ClassificationOrigin.MANUAL.value


def _default_type(amount_minor: int) -> TransactionType:
    return TransactionType.INCOME if amount_minor >= 0 else TransactionType.EXPENSE


def _rule_sort_key(rule: Rule) -> tuple[int, int, str]:
    """Sort key: user rules first, then ascending priority, then rule id.

    The id component is the deterministic tie-break when priorities are equal.
    """
    group = 0 if rule.origin == RuleOrigin.USER.value else 1
    return (group, rule.priority, str(rule.id))


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


@dataclass(frozen=True)
class ClassificationResult:
    """Counts for one classification run over a batch of pending transactions.

    ``classified_by_rule`` and ``classified_by_history`` count transactions that
    received a category from that layer. ``classified_by_default`` counts
    transactions that received only their cost center from the account default
    (they stay unclassified and in the queue). ``unclassified`` counts
    transactions that still have no category after all layers.
    """

    classified_by_rule: int = 0
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
    engine never validates anything and never touches validated transactions.
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

    classified_by_rule = _apply_rules(session, targets, previous_rule_ids)
    remaining = [txn for txn in targets if txn.category_id is None]
    classified_by_history = _apply_history(session, remaining)
    classified_by_default = _apply_default_cost_center(session, targets)
    unclassified = sum(1 for txn in targets if txn.category_id is None)

    session.commit()
    return ClassificationResult(
        classified_by_rule=classified_by_rule,
        classified_by_history=classified_by_history,
        classified_by_default=classified_by_default,
        unclassified=unclassified,
    )


def reclassify_pending(
    session: Session,
    transaction_ids: Sequence[UUID] | None = None,
) -> ClassificationResult:
    """Reset engine-derived classifications on pending rows, then reclassify.

    Only pending, unpaired transactions whose classification came from the
    engine (rules or history) or that are still unclassified are reset. A
    pending row the user already classified by hand (``origin`` manual or
    template with a category set) is left untouched, as are validated rows.
    A rule's hit is only counted when a row moves to a different rule.
    """
    targets = _load_reclassify_targets(session, transaction_ids)
    previous_rule_ids = {txn.id: txn.rule_id for txn in targets}
    for txn in targets:
        txn.transaction_type = _default_type(txn.amount_minor).value
        txn.category_id = None
        txn.cost_center_id = None
        txn.origin = _ORIGIN_PLACEHOLDER
        txn.rule_id = None
    return _classify(session, transaction_ids, previous_rule_ids=previous_rule_ids)


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


@dataclass(frozen=True)
class ReapplyChange:
    """One difference a rules reapply would make to a validated transaction."""

    transaction_id: UUID
    rule_id: UUID
    transaction_type: str
    category_id: UUID
    cost_center_id: UUID | None


def preview_reapply_validated(session: Session) -> list[ReapplyChange]:
    """Return the changes a rules reapply would make to validated history.

    Nothing is written. A validated transaction appears only when a current
    active rule now matches it and would classify it differently; validated
    transactions that no longer match any rule are left as they are.
    """
    validated = list(
        session.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.paired_transaction_id.is_(None),
            )
        ).scalars()
    )
    rules = sorted_active_rules(session)
    changes: list[ReapplyChange] = []
    for txn in validated:
        rule = _first_matching_rule(rules, txn)
        if rule is None:
            continue
        # A rule without a cost center keeps the transaction's cost center.
        cost_center_id = (
            rule.cost_center_id
            if rule.cost_center_id is not None
            else txn.cost_center_id
        )
        if (
            txn.transaction_type == rule.transaction_type
            and txn.category_id == rule.category_id
            and txn.cost_center_id == cost_center_id
        ):
            continue
        changes.append(
            ReapplyChange(
                transaction_id=txn.id,
                rule_id=rule.id,
                transaction_type=rule.transaction_type,
                category_id=rule.category_id,
                cost_center_id=cost_center_id,
            )
        )
    return changes


def apply_reapply_validated(session: Session, changes: Sequence[ReapplyChange]) -> None:
    """Apply a previously computed reapply preview to validated transactions.

    Each change is applied only while its transaction is still validated.
    """
    by_rule: dict[UUID, list[ReapplyChange]] = {}
    for change in changes:
        by_rule.setdefault(change.rule_id, []).append(change)

    for rule_id, rule_changes in by_rule.items():
        rule = session.get(Rule, rule_id)
        if rule is None:
            continue
        for change in rule_changes:
            txn = session.get(Transaction, change.transaction_id)
            if txn is None or txn.status != TransactionStatus.VALIDATED.value:
                continue
            txn.transaction_type = change.transaction_type
            txn.category_id = change.category_id
            txn.cost_center_id = change.cost_center_id
            txn.origin = ClassificationOrigin.RULE.value
            txn.rule_id = rule_id
            rule.hit_count += 1
            rule.last_hit_at = datetime.now(UTC)
    session.commit()


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
    statement = select(Transaction).where(
        Transaction.status == TransactionStatus.PENDING.value,
        Transaction.paired_transaction_id.is_(None),
        or_(
            Transaction.category_id.is_(None),
            Transaction.origin.in_(
                (
                    ClassificationOrigin.RULE.value,
                    ClassificationOrigin.HISTORY.value,
                )
            ),
        ),
    )
    if transaction_ids is not None:
        statement = statement.where(Transaction.id.in_(transaction_ids))
    return list(session.execute(statement).scalars())


def sorted_active_rules(session: Session) -> list[Rule]:
    """Return active rules in precedence order."""
    rules = list(
        session.execute(select(Rule).where(Rule.is_active.is_(True))).scalars()
    )
    rules.sort(key=_rule_sort_key)
    return rules


def rule_to_condition(rule: Rule) -> RuleCondition:
    """Convert a persisted rule into its framework-free matching condition."""
    return RuleCondition(
        mode=RuleMatchMode(rule.mode),
        pattern=rule.pattern,
        amount_min_minor=rule.amount_min_minor,
        amount_max_minor=rule.amount_max_minor,
        day_of_month_min=rule.day_of_month_min,
        day_of_month_max=rule.day_of_month_max,
    )


def _rule_matches(rule: Rule, txn: Transaction) -> bool:
    return condition_matches(
        rule_to_condition(rule),
        normalized_description=txn.normalized_description,
        counterparty=txn.counterparty,
        amount_minor=txn.amount_minor,
        operation_date=txn.operation_date,
    )


def _first_matching_rule(rules: list[Rule], txn: Transaction) -> Rule | None:
    for rule in rules:
        if _rule_matches(rule, txn):
            return rule
    return None


def _apply_rule(txn: Transaction, rule: Rule) -> None:
    txn.transaction_type = rule.transaction_type
    txn.category_id = rule.category_id
    txn.cost_center_id = rule.cost_center_id
    txn.origin = ClassificationOrigin.RULE.value
    txn.rule_id = rule.id


def _apply_rules(
    session: Session,
    targets: list[Transaction],
    previous_rule_ids: Mapping[UUID, UUID | None],
) -> int:
    rules = sorted_active_rules(session)
    count = 0
    for txn in targets:
        rule = _first_matching_rule(rules, txn)
        if rule is not None:
            _apply_rule(txn, rule)
            if previous_rule_ids.get(txn.id) != rule.id:
                rule.hit_count += 1
                rule.last_hit_at = datetime.now(UTC)
            count += 1
    return count


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


def _apply_history(session: Session, targets: list[Transaction]) -> int:
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


def _apply_default_cost_center(session: Session, targets: list[Transaction]) -> int:
    """Fill the account default cost center when no earlier layer set one.

    Only the cost center is written, never type or category, and an existing
    cost center is never overridden.
    """
    account_ids = {txn.account_id for txn in targets}
    accounts = list(
        session.execute(select(Account).where(Account.id.in_(account_ids))).scalars()
    )
    default_by_account = {
        account.id: account.default_cost_center_id
        for account in accounts
        if account.default_cost_center_id is not None
    }
    count = 0
    for txn in targets:
        if txn.cost_center_id is None:
            default_cost_center_id = default_by_account.get(txn.account_id)
            if default_cost_center_id is not None:
                txn.cost_center_id = default_cost_center_id
                if txn.category_id is None:
                    count += 1
    return count
