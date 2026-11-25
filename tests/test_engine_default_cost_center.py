"""Tests for the account default cost-center layer."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.classification import classify_pending
from fiskal.domain.enums import ClassificationOrigin, TransactionType
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _rule,
    _transaction,
)


def test_default_cost_center_only_fills_cost_center(session: Session) -> None:
    """The account default fills only the cost center, never type or category."""
    cost_center = _cost_center(session)
    account = _account(session, default_cc=cost_center)
    txn = _transaction(session, account=account, amount_minor=-500)

    result = classify_pending(session)

    session.refresh(txn)
    assert result.classified_by_default == 1
    assert result.unclassified == 1
    assert txn.cost_center_id == cost_center.id
    assert txn.category_id is None
    assert txn.transaction_type == TransactionType.EXPENSE.value
    assert txn.origin == ClassificationOrigin.MANUAL.value


def test_default_cost_center_count_excludes_categorised_rows(
    session: Session,
) -> None:
    """Rows a rule categorised are not counted as classified by the default."""
    cost_center = _cost_center(session)
    account = _account(session, default_cc=cost_center)
    category = _category(session)
    _rule(session, category=category)
    txn = _transaction(session, account=account)

    result = classify_pending(session)

    session.refresh(txn)
    assert txn.cost_center_id == cost_center.id
    assert result.classified_by_rule == 1
    assert result.classified_by_default == 0


def test_default_cost_center_never_overrides_earlier_cost_center(
    session: Session,
) -> None:
    """A cost center already set by a rule is not replaced by the default."""
    default_cc = _cost_center(session, key="costcenter.personal")
    rule_cc = _cost_center(session, key="costcenter.household")
    account = _account(session, default_cc=default_cc)
    category = _category(session)
    _rule(session, category=category, cost_center=rule_cc)
    txn = _transaction(session, account=account)

    classify_pending(session)

    session.refresh(txn)
    assert txn.cost_center_id == rule_cc.id
