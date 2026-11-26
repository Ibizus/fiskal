"""The deterministic rule cascade layer of the classification engine.

This module owns rule precedence and matching. It exposes the
precedence-ordered active rules and the rule-to-condition conversion used by
the rule service, plus the apply step that stamps a matching rule onto a
pending transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiskal.db.models import Rule, Transaction
from fiskal.domain.enums import ClassificationOrigin, RuleMatchMode, RuleOrigin
from fiskal.domain.rule_matching import RuleCondition, condition_matches


def _rule_sort_key(rule: Rule) -> tuple[int, int, str]:
    """Sort key: user rules first, then ascending priority, then rule id.

    The id component is the deterministic tie-break when priorities are equal.
    """
    group = 0 if rule.origin == RuleOrigin.USER.value else 1
    return (group, rule.priority, str(rule.id))


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


def first_matching_rule(rules: list[Rule], txn: Transaction) -> Rule | None:
    """Return the first rule in precedence order that matches ``txn``."""
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


def apply_rules(
    session: Session,
    targets: list[Transaction],
    previous_rule_ids: Mapping[UUID, UUID | None],
) -> int:
    """Apply the first matching rule to each target and return the count.

    A rule's hit is only counted when a row moves to a different rule, so a
    reclassification over an unchanged match does not inflate the hit count.
    """
    rules = sorted_active_rules(session)
    count = 0
    for txn in targets:
        rule = first_matching_rule(rules, txn)
        if rule is not None:
            _apply_rule(txn, rule)
            if previous_rule_ids.get(txn.id) != rule.id:
                rule.hit_count += 1
                rule.last_hit_at = datetime.now(UTC)
            count += 1
    return count
