"""Tests for rule CRUD, correction proposals and collision reporting."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import uuid4

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
from fiskal.db.models import Category
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    RuleMatchMode,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.rule_matching import RuleCondition, RuleValidationError
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _person,
    _rule,
    _transaction,
)


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
                cost_center_id=None,
                person_id=None,
                amount_min_minor=None,
                amount_max_minor=None,
                day_of_month_min=None,
                day_of_month_max=None,
                is_active=True,
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
                cost_center_id=None,
                person_id=None,
                amount_min_minor=None,
                amount_max_minor=None,
                day_of_month_min=None,
                day_of_month_max=None,
                is_active=True,
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
                cost_center_id=None,
                person_id=None,
                amount_min_minor=None,
                amount_max_minor=None,
                day_of_month_min=None,
                day_of_month_max=None,
                is_active=True,
            ),
        )


def test_update_rule_cannot_omit_fields_and_reset_them(session: Session) -> None:
    """An update must state every field; omitted fields never reset silently."""
    category = _category(session)
    cost_center = _cost_center(session)
    rule = _rule(session, category=category)
    deactivated = RuleSpec(
        priority=1,
        mode=RuleMatchMode.CONTAINS,
        pattern="grocery",
        transaction_type=TransactionType.EXPENSE,
        category_id=category.id,
        cost_center_id=cost_center.id,
        person_id=None,
        amount_min_minor=100,
        amount_max_minor=900,
        day_of_month_min=1,
        day_of_month_max=10,
        is_active=False,
    )
    update_rule(session, rule.id, deactivated)

    partial: dict[str, Any] = {
        "priority": 1,
        "mode": RuleMatchMode.CONTAINS,
        "pattern": "market",
        "transaction_type": TransactionType.EXPENSE,
        "category_id": category.id,
    }
    with pytest.raises(TypeError):
        update_rule(session, rule.id, RuleSpec(**partial))

    update_rule(
        session, rule.id, RuleSpec(**{**asdict(deactivated), "pattern": "market"})
    )

    session.refresh(rule)
    assert rule.pattern == "market"
    assert rule.is_active is False
    assert rule.cost_center_id == cost_center.id
    assert (rule.amount_min_minor, rule.amount_max_minor) == (100, 900)
    assert (rule.day_of_month_min, rule.day_of_month_max) == (1, 10)


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
            cost_center_id=None,
            person_id=None,
            amount_min_minor=None,
            amount_max_minor=None,
            day_of_month_min=None,
            day_of_month_max=None,
            is_active=True,
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


def test_create_rule_rejects_unknown_person(session: Session) -> None:
    """A rule that names a missing person is rejected."""
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    with pytest.raises(RuleValidationError, match="person does not exist"):
        create_rule(
            session,
            RuleSpec(
                priority=1,
                mode=RuleMatchMode.CONTAINS,
                pattern="salary",
                transaction_type=TransactionType.INCOME,
                category_id=income.id,
                cost_center_id=None,
                person_id=uuid4(),
                amount_min_minor=None,
                amount_max_minor=None,
                day_of_month_min=None,
                day_of_month_max=None,
                is_active=True,
            ),
        )


def test_create_and_update_rule_carry_person(session: Session) -> None:
    """A rule's person attribution is stored on create and replaced on update."""
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    ada = _person(session, name="Ada")
    grace = _person(session, name="Grace")

    rule = create_rule(
        session,
        RuleSpec(
            priority=1,
            mode=RuleMatchMode.CONTAINS,
            pattern="salary",
            transaction_type=TransactionType.INCOME,
            category_id=income.id,
            cost_center_id=None,
            person_id=ada.id,
            amount_min_minor=None,
            amount_max_minor=None,
            day_of_month_min=None,
            day_of_month_max=None,
            is_active=True,
        ),
    )
    assert rule.person_id == ada.id

    update_rule(
        session,
        rule.id,
        RuleSpec(
            priority=1,
            mode=RuleMatchMode.CONTAINS,
            pattern="salary",
            transaction_type=TransactionType.INCOME,
            category_id=income.id,
            cost_center_id=None,
            person_id=grace.id,
            amount_min_minor=None,
            amount_max_minor=None,
            day_of_month_min=None,
            day_of_month_max=None,
            is_active=True,
        ),
    )

    session.refresh(rule)
    assert rule.person_id == grace.id


def _income_spec(category: Category, *, pattern: str, is_active: bool) -> RuleSpec:
    return RuleSpec(
        priority=1,
        mode=RuleMatchMode.CONTAINS,
        pattern=pattern,
        transaction_type=TransactionType.INCOME,
        category_id=category.id,
        cost_center_id=None,
        person_id=None,
        amount_min_minor=None,
        amount_max_minor=None,
        day_of_month_min=None,
        day_of_month_max=None,
        is_active=is_active,
    )


@pytest.mark.parametrize("change", ["edit", "deactivate", "delete"])
def test_rule_change_clears_rule_set_person(session: Session, change: str) -> None:
    """A rule's person attribution does not survive once the rule stops applying.

    A person the user set by hand on another pending row is kept.
    """
    account = _account(session)
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    ada = _person(session, name="Ada")
    grace = _person(session, name="Grace")
    rule = _rule(
        session,
        pattern="salary",
        transaction_type="income",
        category=income,
        person=ada,
    )
    by_rule = _transaction(
        session,
        account=account,
        description="monthly salary",
        counterparty=None,
        amount_minor=2_000,
    )
    by_hand = _transaction(
        session,
        account=account,
        description="birthday gift",
        counterparty=None,
        amount_minor=3_000,
        person=grace,
    )
    classify_pending(session)
    session.refresh(by_rule)
    assert by_rule.person_id == ada.id

    if change == "edit":
        update_rule(
            session, rule.id, _income_spec(income, pattern="rent", is_active=True)
        )
    elif change == "deactivate":
        update_rule(
            session, rule.id, _income_spec(income, pattern="salary", is_active=False)
        )
    else:
        delete_rule(session, rule.id)

    session.refresh(by_rule)
    session.refresh(by_hand)
    assert by_rule.rule_id is None
    assert by_rule.category_id is None
    assert by_rule.person_id is None
    assert by_hand.person_id == grace.id
