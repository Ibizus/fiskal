"""Tests for the rule cascade and pending-reclassification orchestration."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleSpec,
    classify_pending,
    create_rule,
    reclassify_pending,
    update_rule,
)
from fiskal.domain.enums import (
    ClassificationOrigin,
    RuleOrigin,
    TransactionStatus,
    TransactionType,
)
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _rule,
    _transaction,
)


def test_rule_layer_classifies_and_records_origin(session: Session) -> None:
    """A matching rule sets type, cost center, category, origin and rule id."""
    account = _account(session)
    category = _category(session)
    cost_center = _cost_center(session)
    rule = _rule(session, category=category, cost_center=cost_center)
    txn = _transaction(session, account=account)

    result = classify_pending(session)

    session.refresh(txn)
    assert result.classified_by_rule == 1
    assert result.unclassified == 0
    assert txn.category_id == category.id
    assert txn.cost_center_id == cost_center.id
    assert txn.transaction_type == TransactionType.EXPENSE.value
    assert txn.origin == ClassificationOrigin.RULE.value
    assert txn.rule_id == rule.id


def test_rule_hit_count_and_last_hit_are_updated(session: Session) -> None:
    """A matched rule records a hit and the time of the hit."""
    account = _account(session)
    category = _category(session)
    rule = _rule(session, category=category)
    _transaction(session, account=account)

    classify_pending(session)

    session.refresh(rule)
    assert rule.hit_count == 1
    assert rule.last_hit_at is not None


def test_reclassify_does_not_inflate_hit_count(session: Session) -> None:
    """Rule changes that reclassify the queue do not recount existing hits."""
    account = _account(session)
    category = _category(session)
    rule = _rule(session, category=category)
    _transaction(session, account=account)
    classify_pending(session)
    session.refresh(rule)
    first_hit_at = rule.last_hit_at

    other = create_rule(
        session,
        RuleSpec(
            priority=5,
            mode="contains",
            pattern="unrelated",
            transaction_type="expense",
            category_id=category.id,
        ),
    )
    update_rule(
        session,
        other.id,
        RuleSpec(
            priority=6,
            mode="contains",
            pattern="still unrelated",
            transaction_type="expense",
            category_id=category.id,
        ),
    )
    reclassify_pending(session)

    session.refresh(rule)
    assert rule.hit_count == 1
    assert rule.last_hit_at == first_hit_at


def test_reclassify_counts_hit_when_row_moves_to_another_rule(
    session: Session,
) -> None:
    """A row that moves to a new winning rule counts one hit for that rule."""
    account = _account(session)
    category = _category(session)
    old = _rule(session, priority=10, category=category)
    _transaction(session, account=account)
    classify_pending(session)

    new = create_rule(
        session,
        RuleSpec(
            priority=1,
            mode="contains",
            pattern="grocery",
            transaction_type="expense",
            category_id=category.id,
        ),
    )

    session.refresh(old)
    session.refresh(new)
    assert old.hit_count == 1
    assert new.hit_count == 1


def test_user_rule_beats_seed_rule_regardless_of_priority(session: Session) -> None:
    """User rules always win over seed rules, whatever the numeric priority."""
    account = _account(session)
    seed_category = _category(session, key="category.misc")
    user_category = _category(session, key="category.food")
    _rule(
        session,
        priority=500,
        pattern="grocery",
        category=user_category,
        origin=RuleOrigin.USER,
    )
    _rule(
        session,
        priority=1,
        pattern="grocery",
        category=seed_category,
        origin=RuleOrigin.SEED,
    )
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    assert txn.category_id == user_category.id


def test_lower_priority_wins_within_group(session: Session) -> None:
    """Inside a group the lower numeric priority is matched first."""
    account = _account(session)
    first = _category(session, key="category.food")
    second = _category(session, key="category.misc")
    _rule(session, priority=10, pattern="grocery", category=first)
    _rule(session, priority=1, pattern="grocery", category=second)
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    assert txn.category_id == second.id


def test_priority_tie_breaks_deterministically_on_id(session: Session) -> None:
    """Two equal-priority matches resolve by id, not by insertion order."""
    account = _account(session)
    first = _category(session, key="category.food")
    second = _category(session, key="category.misc")
    rule_a = _rule(session, priority=1, pattern="grocery", category=first)
    rule_b = _rule(session, priority=1, pattern="grocery", category=second)
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    winner = min(rule_a, rule_b, key=lambda rule: str(rule.id))
    assert txn.category_id == winner.category_id


def test_engine_never_touches_validated_transactions(session: Session) -> None:
    """A validated row is left alone even when a rule would match it."""
    account = _account(session)
    category = _category(session)
    _rule(session, category=category)
    txn = _transaction(
        session,
        account=account,
        status=TransactionStatus.VALIDATED,
        category=_category(session, key="category.misc"),
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )
    original_category = txn.category_id

    classify_pending(session)

    session.refresh(txn)
    assert txn.category_id == original_category
    assert txn.origin == ClassificationOrigin.MANUAL.value


def test_reclassify_pending_resets_and_reapplies_rules(session: Session) -> None:
    """Reclassification resets engine-derived pending rows and re-runs rules."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    _rule(session, priority=1, pattern="grocery", category=food)
    txn = _transaction(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)
    assert txn.category_id == food.id

    _rule(session, priority=0, pattern="grocery", category=misc)
    reclassify_pending(session)

    session.refresh(txn)
    assert txn.category_id == misc.id
