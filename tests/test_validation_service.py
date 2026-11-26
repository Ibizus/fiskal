"""Tests for the validation queue service and rule creation from corrections."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from fiskal.classification import (
    ValidationError,
    classify_pending,
    confirm_pair,
    create_rule_from_correction,
    list_pending_transactions,
    reclassify_pending,
    validate_transaction,
)
from fiskal.domain.enums import (
    CategoryKind,
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _person,
    _rule,
    _transaction,
)


def _pending_expense(session: Session, *, account, description: str = "grocery store"):
    return _transaction(
        session,
        account=account,
        description=description,
        counterparty="Fresh Market",
        amount_minor=-1000,
    )


def test_validate_transaction_sets_manual_and_validated(session: Session) -> None:
    """Confirming or correcting marks the row manual and validated."""
    account = _account(session)
    food = _category(session, key="category.food")
    cost_center = _cost_center(session)
    ada = _person(session, name="Ada")
    txn = _pending_expense(session, account=account)

    validate_transaction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=food.id,
        cost_center_id=cost_center.id,
        person_id=ada.id,
    )

    session.refresh(txn)
    assert txn.status == TransactionStatus.VALIDATED.value
    assert txn.origin == ClassificationOrigin.MANUAL.value
    assert txn.transaction_type == TransactionType.EXPENSE.value
    assert txn.category_id == food.id
    assert txn.cost_center_id == cost_center.id
    assert txn.person_id == ada.id
    assert txn.rule_id is None


def test_validate_transaction_survives_reclassification(session: Session) -> None:
    """A validated row is stable under later reclassification."""
    account = _account(session)
    food = _category(session, key="category.food")
    ada = _person(session, name="Ada")
    txn = _pending_expense(session, account=account)

    validate_transaction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=food.id,
        cost_center_id=None,
        person_id=ada.id,
    )

    reclassify_pending(session)

    session.refresh(txn)
    assert txn.category_id == food.id
    assert txn.origin == ClassificationOrigin.MANUAL.value
    assert txn.person_id == ada.id


def test_validate_transaction_person_keeps_manual_origin(session: Session) -> None:
    """Setting a person by hand keeps the origin manual, never rule-owned."""
    account = _account(session)
    food = _category(session, key="category.food")
    ada = _person(session, name="Ada")
    _rule(session, pattern="grocery", category=food, person=ada)
    txn = _pending_expense(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)
    assert txn.origin == ClassificationOrigin.RULE.value
    assert txn.person_id == ada.id

    grace = _person(session, name="Grace")
    validate_transaction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=food.id,
        cost_center_id=None,
        person_id=grace.id,
    )

    session.refresh(txn)
    assert txn.origin == ClassificationOrigin.MANUAL.value
    assert txn.person_id == grace.id
    assert txn.rule_id is None


def test_validate_transaction_rejects_incompatible_category_kind(
    session: Session,
) -> None:
    """A category whose kind disagrees with the type is rejected."""
    account = _account(session)
    income = _category(session, key="category.income", kind=CategoryKind.INCOME)
    txn = _pending_expense(session, account=account)

    with pytest.raises(ValidationError, match="incompatible"):
        validate_transaction(
            session,
            txn.id,
            transaction_type=TransactionType.EXPENSE,
            category_id=income.id,
            cost_center_id=None,
            person_id=None,
        )


def test_validate_transaction_rejects_already_validated(session: Session) -> None:
    """A validated row cannot be validated again."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _transaction(
        session,
        account=account,
        status=TransactionStatus.VALIDATED,
        category=food,
        transaction_type=TransactionType.EXPENSE,
    )

    with pytest.raises(ValidationError, match="already validated"):
        validate_transaction(
            session,
            txn.id,
            transaction_type=TransactionType.EXPENSE,
            category_id=food.id,
            cost_center_id=None,
            person_id=None,
        )


def test_validate_transaction_rejects_internal_transfer(session: Session) -> None:
    """A paired internal transfer is never validated as a category."""
    source = _account(session, name="Source")
    target = _account(session, name="Target")
    outgoing = _transaction(session, account=source, amount_minor=-5000)
    incoming = _transaction(session, account=target, amount_minor=5000)
    confirm_pair(session, outgoing.id, incoming.id)
    food = _category(session, key="category.food")

    with pytest.raises(ValidationError, match="internal transfer"):
        validate_transaction(
            session,
            outgoing.id,
            transaction_type=TransactionType.EXPENSE,
            category_id=food.id,
            cost_center_id=None,
            person_id=None,
        )


def test_list_pending_transactions_excludes_validated_and_paired(
    session: Session,
) -> None:
    """The queue only shows pending, unpaired rows."""
    account = _account(session)
    first = _pending_expense(session, account=account, description="first")
    second = _pending_expense(session, account=account, description="second")
    food = _category(session, key="category.food")
    _transaction(
        session,
        account=account,
        status=TransactionStatus.VALIDATED,
        category=food,
    )
    source = _account(session, name="Source")
    target = _account(session, name="Target")
    left = _transaction(session, account=source, amount_minor=-3000)
    right = _transaction(session, account=target, amount_minor=3000)
    confirm_pair(session, left.id, right.id)

    pending = list_pending_transactions(session)

    assert {txn.id for txn in pending} == {first.id, second.id}


def test_create_rule_from_correction_places_rule_and_validates(
    session: Session,
) -> None:
    """A correction can create a rule above the classifier and then validate."""
    account = _account(session)
    food = _category(session, key="category.food")
    misc = _category(session, key="category.misc")
    rule = _rule(session, pattern="grocery", category=food, priority=5)
    txn = _pending_expense(session, account=account, description="grocery store")
    classify_pending(session)
    session.refresh(txn)
    assert txn.rule_id == rule.id

    created = create_rule_from_correction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=misc.id,
        cost_center_id=None,
        person_id=None,
    )

    assert created.priority == rule.priority - 1
    assert created.pattern == "Fresh Market"
    assert created.category_id == misc.id
    session.refresh(txn)
    assert txn.category_id == misc.id
    assert txn.origin == ClassificationOrigin.RULE.value
    assert txn.rule_id == created.id

    validate_transaction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=misc.id,
        cost_center_id=None,
        person_id=None,
    )
    session.refresh(txn)
    assert txn.status == TransactionStatus.VALIDATED.value
    assert txn.origin == ClassificationOrigin.MANUAL.value
    assert txn.category_id == misc.id


def test_create_rule_from_correction_pattern_override(session: Session) -> None:
    """The proposed pattern can be overridden by the rule editor."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _pending_expense(session, account=account)

    created = create_rule_from_correction(
        session,
        txn.id,
        transaction_type=TransactionType.EXPENSE,
        category_id=food.id,
        cost_center_id=None,
        person_id=None,
        pattern="fresh market purchase",
    )

    assert created.pattern == "fresh market purchase"


def test_validate_transaction_rejects_unknown_type(session: Session) -> None:
    """An unknown transaction type is rejected before any write."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _pending_expense(session, account=account)

    with pytest.raises(ValidationError, match="unknown transaction type"):
        validate_transaction(
            session,
            txn.id,
            transaction_type="mystery",
            category_id=food.id,
            cost_center_id=None,
            person_id=None,
        )


def test_validate_transaction_rejects_missing_transaction(session: Session) -> None:
    """A missing transaction id is rejected."""
    food = _category(session, key="category.food")

    with pytest.raises(ValidationError, match="transaction not found"):
        validate_transaction(
            session,
            uuid4(),
            transaction_type=TransactionType.EXPENSE,
            category_id=food.id,
            cost_center_id=None,
            person_id=None,
        )


def test_validate_transaction_rejects_missing_category(session: Session) -> None:
    """A category that does not exist is rejected."""
    account = _account(session)
    txn = _pending_expense(session, account=account)

    with pytest.raises(ValidationError, match="category does not exist"):
        validate_transaction(
            session,
            txn.id,
            transaction_type=TransactionType.EXPENSE,
            category_id=uuid4(),
            cost_center_id=None,
            person_id=None,
        )


def test_validate_transaction_rejects_missing_cost_center(session: Session) -> None:
    """A cost center that does not exist is rejected."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _pending_expense(session, account=account)

    with pytest.raises(ValidationError, match="cost center does not exist"):
        validate_transaction(
            session,
            txn.id,
            transaction_type=TransactionType.EXPENSE,
            category_id=food.id,
            cost_center_id=uuid4(),
            person_id=None,
        )


def test_validate_transaction_rejects_missing_person(session: Session) -> None:
    """A person that does not exist is rejected."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _pending_expense(session, account=account)

    with pytest.raises(ValidationError, match="person does not exist"):
        validate_transaction(
            session,
            txn.id,
            transaction_type=TransactionType.EXPENSE,
            category_id=food.id,
            cost_center_id=None,
            person_id=uuid4(),
        )


def test_validate_transaction_rejects_internal_transfer_type(
    session: Session,
) -> None:
    """The internal_transfer type is owned by pairing, not validation."""
    account = _account(session)
    food = _category(session, key="category.food")
    txn = _pending_expense(session, account=account)

    with pytest.raises(ValidationError, match="handled by pairing"):
        validate_transaction(
            session,
            txn.id,
            transaction_type=TransactionType.INTERNAL_TRANSFER,
            category_id=food.id,
            cost_center_id=None,
            person_id=None,
        )
