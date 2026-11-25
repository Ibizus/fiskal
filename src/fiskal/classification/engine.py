"""Classification engine: the deterministic cascade for pending transactions.

The layers run in the documented order: deterministic rules, then history, then
the account's default cost center. The optional AI ``Classifier`` layer is a
documented extension point that plugs in between history and the default cost
center; it is not implemented in v1. The engine only writes to pending,
unpaired transactions and never touches validated rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
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
class HistorySuggestion:
    """The most frequent validated classification for an ambiguous counterparty.

    Returned to the (future) validation queue so it can preselect the value;
    the transaction itself stays unclassified and pending.
    """

    transaction_id: UUID
    counterparty: str
    transaction_type: TransactionType
    cost_center_id: UUID | None
    category_id: UUID


@dataclass(frozen=True)
class ClassificationResult:
    """Counts for one classification run over a batch of pending transactions.

    ``classified_by_rule`` and ``classified_by_history`` count transactions that
    received a category from that layer. ``classified_by_default`` counts
    transactions whose cost center was filled by the account default (they stay
    unclassified and in the queue). ``unclassified`` counts transactions that
    still have no category after all layers.
    """

    classified_by_rule: int = 0
    classified_by_history: int = 0
    classified_by_default: int = 0
    unclassified: int = 0
    history_suggestions: tuple[HistorySuggestion, ...] = ()


def classify_pending(
    session: Session,
    transaction_ids: Sequence[UUID] | None = None,
) -> ClassificationResult:
    """Classify pending, unclassified, unpaired transactions through the cascade.

    When ``transaction_ids`` is given, only those transactions are considered;
    otherwise every eligible transaction in the profile is classified. The
    engine never validates anything and never touches validated transactions.
    """
    targets = _load_unclassified(session, transaction_ids)
    if not targets:
        return ClassificationResult()

    classified_by_rule = _apply_rules(session, targets)
    remaining = [txn for txn in targets if txn.category_id is None]
    classified_by_history, suggestions = _apply_history(session, remaining)
    classified_by_default = _apply_default_cost_center(session, targets)
    unclassified = sum(1 for txn in targets if txn.category_id is None)

    session.commit()
    return ClassificationResult(
        classified_by_rule=classified_by_rule,
        classified_by_history=classified_by_history,
        classified_by_default=classified_by_default,
        unclassified=unclassified,
        history_suggestions=tuple(suggestions),
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
    """
    targets = _load_reclassify_targets(session, transaction_ids)
    for txn in targets:
        txn.transaction_type = _default_type(txn.amount_minor).value
        txn.category_id = None
        txn.cost_center_id = None
        txn.origin = _ORIGIN_PLACEHOLDER
        txn.rule_id = None
    return classify_pending(session, transaction_ids)


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
        if (
            txn.transaction_type == rule.transaction_type
            and txn.category_id == rule.category_id
            and txn.cost_center_id == rule.cost_center_id
        ):
            continue
        changes.append(
            ReapplyChange(
                transaction_id=txn.id,
                rule_id=rule.id,
                transaction_type=rule.transaction_type,
                category_id=rule.category_id,
                cost_center_id=rule.cost_center_id,
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


def _apply_rules(session: Session, targets: list[Transaction]) -> int:
    rules = sorted_active_rules(session)
    count = 0
    for txn in targets:
        rule = _first_matching_rule(rules, txn)
        if rule is not None:
            _apply_rule(txn, rule)
            rule.hit_count += 1
            rule.last_hit_at = datetime.now(UTC)
            count += 1
    return count


def _apply_history(
    session: Session, targets: list[Transaction]
) -> tuple[int, list[HistorySuggestion]]:
    """Classify from validated history when a counterparty is unambiguous.

    A counterparty with a single validated outcome is applied (origin history).
    A counterparty with several outcomes stays unclassified; its most frequent
    outcome is returned as a suggestion for the validation queue.
    """
    if not targets:
        return 0, []

    counterparts = {txn.counterparty for txn in targets if txn.counterparty}
    if not counterparts:
        return 0, []

    validated = list(
        session.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.VALIDATED.value,
                Transaction.category_id.is_not(None),
                Transaction.counterparty.in_(counterparts),
            )
        ).scalars()
    )

    history: dict[str, Counter[tuple[str, UUID | None, UUID | None]]] = {}
    for row in validated:
        counterparty = row.counterparty
        if counterparty is None:
            continue
        history.setdefault(counterparty, Counter())[
            (row.transaction_type, row.cost_center_id, row.category_id)
        ] += 1

    count = 0
    suggestions: list[HistorySuggestion] = []
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
            if category_id is None:
                continue
            txn.transaction_type = outcome_type
            txn.cost_center_id = cost_center_id
            txn.category_id = category_id
            txn.origin = ClassificationOrigin.HISTORY.value
            txn.rule_id = None
            count += 1
        else:
            ((outcome_type, cost_center_id, category_id), _) = outcomes.most_common(1)[
                0
            ]
            if category_id is None:
                continue
            suggestions.append(
                HistorySuggestion(
                    transaction_id=txn.id,
                    counterparty=txn.counterparty,
                    transaction_type=TransactionType(outcome_type),
                    cost_center_id=cost_center_id,
                    category_id=category_id,
                )
            )
    return count, suggestions


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
                count += 1
    return count
