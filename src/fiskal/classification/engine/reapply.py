"""Explicit bulk reapply of rules over validated history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.classification.engine.rule_cascade import (
    first_matching_rule,
    sorted_active_rules,
)
from fiskal.db.models import Rule, Transaction
from fiskal.domain.enums import ClassificationOrigin, TransactionStatus


@dataclass(frozen=True)
class ReapplyChange:
    """One difference a rules reapply would make to a validated transaction."""

    transaction_id: UUID
    rule_id: UUID
    transaction_type: str
    category_id: UUID
    cost_center_id: UUID | None
    person_id: UUID | None


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
        rule = first_matching_rule(rules, txn)
        if rule is None:
            continue
        # A rule without a cost center keeps the transaction's cost center.
        cost_center_id = (
            rule.cost_center_id
            if rule.cost_center_id is not None
            else txn.cost_center_id
        )
        # A rule without a person keeps the transaction's attribution.
        person_id = rule.person_id if rule.person_id is not None else txn.person_id
        if (
            txn.transaction_type == rule.transaction_type
            and txn.category_id == rule.category_id
            and txn.cost_center_id == cost_center_id
            and txn.person_id == person_id
        ):
            continue
        changes.append(
            ReapplyChange(
                transaction_id=txn.id,
                rule_id=rule.id,
                transaction_type=rule.transaction_type,
                category_id=rule.category_id,
                cost_center_id=cost_center_id,
                person_id=person_id,
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
            txn.person_id = change.person_id
            txn.origin = ClassificationOrigin.RULE.value
            txn.rule_id = rule_id
            rule.hit_count += 1
            rule.last_hit_at = datetime.now(UTC)
    session.commit()
