"""Tests for the validated-history layer and its suggestions."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleSpec,
    classify_pending,
    create_rule,
    suggest_from_history,
)
from fiskal.domain.enums import (
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)
from tests.support.factories import _account, _category, _cost_center, _transaction

FOOD_VISITS = 3


def test_history_single_outcome_classifies(session: Session) -> None:
    """A counterparty validated only one way is proposed with origin history."""
    account = _account(session)
    category = _category(session)
    cost_center = _cost_center(session)
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        origin=ClassificationOrigin.HISTORY,
        category=category,
        cost_center=cost_center,
        transaction_type=TransactionType.EXPENSE,
    )
    pending = _transaction(session, account=account, counterparty="Fresh Market")

    result = classify_pending(session)

    session.refresh(pending)
    assert result.classified_by_history == 1
    assert pending.category_id == category.id
    assert pending.cost_center_id == cost_center.id
    assert pending.origin == ClassificationOrigin.HISTORY.value


def test_history_several_outcomes_stays_unclassified(session: Session) -> None:
    """Several validated outcomes leave the transaction in the queue."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=food,
        transaction_type=TransactionType.EXPENSE,
    )
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=misc,
        transaction_type=TransactionType.EXPENSE,
    )
    pending = _transaction(session, account=account, counterparty="Fresh Market")

    result = classify_pending(session)

    session.refresh(pending)
    assert pending.category_id is None
    assert result.classified_by_history == 0
    assert result.unclassified == 1
    suggestion = suggest_from_history(session, pending.id)
    assert suggestion is not None
    assert suggestion.transaction_id == pending.id
    assert {suggestion.preselected.category_id} | {
        alt.category_id for alt in suggestion.alternatives
    } == {food.id, misc.id}


def test_history_most_frequent_outcome_is_suggested(session: Session) -> None:
    """The most frequent validated outcome is preselected, with alternatives."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    for _ in range(FOOD_VISITS):
        _transaction(
            session,
            account=account,
            counterparty="Fresh Market",
            status=TransactionStatus.VALIDATED,
            category=food,
            transaction_type=TransactionType.EXPENSE,
        )
    _transaction(
        session,
        account=account,
        counterparty="Fresh Market",
        status=TransactionStatus.VALIDATED,
        category=misc,
        transaction_type=TransactionType.EXPENSE,
    )
    pending = _transaction(session, account=account, counterparty="Fresh Market")

    classify_pending(session)
    suggestion = suggest_from_history(session, pending.id)

    assert suggestion is not None
    assert suggestion.counterparty == "Fresh Market"
    assert suggestion.preselected.category_id == food.id
    assert suggestion.preselected.count == FOOD_VISITS
    assert suggestion.preselected.transaction_type == TransactionType.EXPENSE
    assert [(alt.category_id, alt.count) for alt in suggestion.alternatives] == [
        (misc.id, 1)
    ]


def test_history_suggestion_survives_rule_changes(session: Session) -> None:
    """The suggestion is recomputed after a rule change reclassifies the queue."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    for category in (food, food, misc):
        _transaction(
            session,
            account=account,
            counterparty="Fresh Market",
            status=TransactionStatus.VALIDATED,
            category=category,
            transaction_type=TransactionType.EXPENSE,
        )
    pending = _transaction(session, account=account, counterparty="Fresh Market")
    classify_pending(session)

    create_rule(
        session,
        RuleSpec(
            priority=1,
            mode="contains",
            pattern="unrelated",
            transaction_type="expense",
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

    suggestion = suggest_from_history(session, pending.id)
    assert suggestion is not None
    assert suggestion.preselected.category_id == food.id


def test_history_suggestion_is_none_without_history(session: Session) -> None:
    """No suggestion for unknown, validated, or history-less transactions."""
    account = _account(session)
    food = _category(session)
    pending = _transaction(session, account=account, counterparty="Nobody")
    validated = _transaction(
        session,
        account=account,
        counterparty="Nobody",
        status=TransactionStatus.VALIDATED,
        category=food,
    )

    assert suggest_from_history(session, pending.id) is not None
    assert suggest_from_history(session, validated.id) is None
    assert suggest_from_history(session, uuid4()) is None
    other = _transaction(session, account=account, counterparty="Someone Else")
    assert suggest_from_history(session, other.id) is None
