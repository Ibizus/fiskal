"""Tests for the explicit rules reapply over validated history."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.classification import apply_reapply_validated, preview_reapply_validated
from fiskal.domain.enums import ClassificationOrigin, TransactionStatus, TransactionType
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _rule,
    _transaction,
)


def test_preview_reapply_validated_reports_without_writing(session: Session) -> None:
    """The reapply preview lists changes but does not modify anything."""
    account = _account(session)
    food = _category(session, key="category.food")
    shopping = _category(session, key="category.shopping")
    rule = _rule(session, category=shopping)
    txn = _transaction(
        session,
        account=account,
        description="grocery store",
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )

    changes = preview_reapply_validated(session)

    assert len(changes) == 1
    assert changes[0].transaction_id == txn.id
    assert changes[0].rule_id == rule.id
    assert changes[0].category_id == shopping.id
    session.refresh(txn)
    assert txn.category_id == food.id
    assert txn.origin == ClassificationOrigin.MANUAL.value


def test_apply_reapply_validated_writes_changes(session: Session) -> None:
    """Applying a preview updates validated rows and the rule's hit count."""
    account = _account(session)
    food = _category(session, key="category.food")
    shopping = _category(session, key="category.shopping")
    rule = _rule(session, category=shopping)
    txn = _transaction(
        session,
        account=account,
        description="grocery store",
        status=TransactionStatus.VALIDATED,
        category=food,
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )

    changes = preview_reapply_validated(session)
    apply_reapply_validated(session, changes)

    session.refresh(txn)
    session.refresh(rule)
    assert txn.category_id == shopping.id
    assert txn.origin == ClassificationOrigin.RULE.value
    assert txn.rule_id == rule.id
    assert rule.hit_count == 1


def test_reapply_validated_keeps_cost_center_when_rule_has_none(
    session: Session,
) -> None:
    """A rule without a cost center neither reports nor erases a cost center."""
    household = _cost_center(session)
    account = _account(session)
    food = _category(session, key="category.food")
    shopping = _category(session, key="category.shopping")
    _rule(session, category=food)
    unchanged = _transaction(
        session,
        account=account,
        description="grocery store",
        status=TransactionStatus.VALIDATED,
        category=food,
        cost_center=household,
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )
    changed = _transaction(
        session,
        account=account,
        description="grocery store",
        day=16,
        status=TransactionStatus.VALIDATED,
        category=shopping,
        cost_center=household,
        origin=ClassificationOrigin.MANUAL,
        transaction_type=TransactionType.EXPENSE,
    )

    changes = preview_reapply_validated(session)

    assert [change.transaction_id for change in changes] == [changed.id]
    apply_reapply_validated(session, changes)
    session.refresh(unchanged)
    session.refresh(changed)
    assert changed.category_id == food.id
    assert changed.cost_center_id == household.id
    assert unchanged.cost_center_id == household.id
