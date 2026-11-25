"""Tests for rule CRUD, correction proposals and collision reporting."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleSpec,
    classify_pending,
    create_rule,
    delete_rule,
    find_rule_collisions,
    propose_rule_from_correction,
    update_rule,
)
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    RuleMatchMode,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.rule_matching import RuleCondition, RuleValidationError
from tests.support.factories import _account, _category, _rule, _transaction


def test_create_rule_validates_regex(session: Session) -> None:
    """Creating a rule with a pathological regex is rejected."""
    category = _category(session)
    with pytest.raises(RuleValidationError):
        create_rule(
            session,
            RuleSpec(
                priority=1,
                mode=RuleMatchMode.REGEX,
                pattern="(a+)+",
                transaction_type=TransactionType.EXPENSE,
                category_id=category.id,
            ),
        )


def test_create_rule_rejects_internal_transfer_type(session: Session) -> None:
    """Rules never assign the internal_transfer type."""
    category = _category(session)
    with pytest.raises(RuleValidationError, match="internal_transfer"):
        create_rule(
            session,
            RuleSpec(
                priority=1,
                mode=RuleMatchMode.CONTAINS,
                pattern="transfer",
                transaction_type=TransactionType.INTERNAL_TRANSFER,
                category_id=category.id,
            ),
        )


def test_create_rule_rejects_incompatible_category_kind(session: Session) -> None:
    """A rule's type and category kind must agree."""
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    with pytest.raises(RuleValidationError, match="incompatible"):
        create_rule(
            session,
            RuleSpec(
                priority=1,
                mode=RuleMatchMode.CONTAINS,
                pattern="salary",
                transaction_type=TransactionType.EXPENSE,
                category_id=income.id,
            ),
        )


def test_update_rule_reclassifies_only_pending(session: Session) -> None:
    """Updating a rule reclassifies pending rows and leaves validated ones."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    rule = _rule(session, pattern="grocery", category=food)
    pending = _transaction(session, account=account, description="grocery store")
    validated = _transaction(
        session,
        account=account,
        description="grocery store",
        counterparty=None,
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.RULE,
        transaction_type=TransactionType.EXPENSE,
    )
    classify_pending(session)

    update_rule(
        session,
        rule.id,
        RuleSpec(
            priority=1,
            mode=RuleMatchMode.CONTAINS,
            pattern="rent",
            transaction_type=TransactionType.EXPENSE,
            category_id=misc.id,
        ),
    )

    session.refresh(pending)
    session.refresh(validated)
    assert pending.category_id is None
    assert validated.category_id == food.id


def test_delete_rule_clears_rule_link_and_reclassifies(session: Session) -> None:
    """Deleting a rule clears its link and reclassifies pending rows."""
    account = _account(session)
    category = _category(session)
    rule = _rule(session, category=category)
    txn = _transaction(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)
    assert txn.rule_id == rule.id

    delete_rule(session, rule.id)

    session.refresh(txn)
    assert txn.rule_id is None
    assert txn.category_id is None


def test_propose_rule_from_correction_places_above_classifier(
    session: Session,
) -> None:
    """A correction proposes an editable contains rule above the classifier."""
    account = _account(session)
    category = _category(session)
    priority = 5
    rule = _rule(session, priority=priority, category=category)
    txn = _transaction(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)

    proposal = propose_rule_from_correction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=category.id,
        cost_center_id=None,
    )

    assert proposal.mode is RuleMatchMode.CONTAINS
    assert proposal.pattern == "Fresh Market"
    assert proposal.priority == rule.priority - 1
    session.refresh(rule)
    assert rule.priority == priority


def test_find_rule_collisions_reports_overlap(session: Session) -> None:
    """The service helper reports overlapping active rules."""
    category = _category(session)
    _rule(session, pattern="grocery store", category=category)
    collisions = find_rule_collisions(
        session, RuleCondition(mode=RuleMatchMode.CONTAINS, pattern="grocery")
    )
    assert len(collisions) == 1
