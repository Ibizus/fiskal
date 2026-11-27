"""Tests for the filtered transaction listing service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.db.models import Category
from fiskal.domain.enums import (
    CategoryKind,
    CategoryNature,
    TransactionStatus,
    TransactionType,
)
from fiskal.reports.helpers import TransactionFilter, list_transactions
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _transaction,
)


def test_list_transactions_filters_by_account(session: Session) -> None:
    """Only the chosen account's transactions are returned."""
    first = _account(session, name="First")
    second = _account(session, name="Second")
    _transaction(session, account=first, description="first movement")
    _transaction(session, account=second, description="second movement")

    result = list_transactions(session, TransactionFilter(account_id=first.id))

    assert [txn.account_id for txn in result] == [first.id]
    assert len(result) == 1


def test_list_transactions_filters_by_type_status_and_query(session: Session) -> None:
    """Type, status and text filters combine."""
    account = _account(session)
    food = _category(session, key="category.food")
    pending = _transaction(session, account=account, description="grocery store")
    _transaction(
        session,
        account=account,
        description="cash withdrawal",
        status=TransactionStatus.VALIDATED,
        category=food,
        transaction_type=TransactionType.EXPENSE,
    )

    result = list_transactions(
        session,
        TransactionFilter(
            transaction_type=TransactionType.EXPENSE.value,
            status=TransactionStatus.PENDING.value,
            query="grocery",
        ),
    )

    assert [txn.id for txn in result] == [pending.id]


def test_list_transactions_returns_newest_first(session: Session) -> None:
    """Rows are ordered newest first."""
    account = _account(session)
    older = _transaction(session, account=account, day=1, description="older")
    newer = _transaction(session, account=account, day=28, description="newer")

    result = list_transactions(session)

    assert [txn.id for txn in result] == [newer.id, older.id]


def _fixed_category(session: Session, key: str) -> Category:
    category = Category(
        name_key=key,
        kind=CategoryKind.EXPENSE.value,
        nature=CategoryNature.FIXED.value,
    )
    session.add(category)
    session.commit()
    return category


def test_list_transactions_filters_by_category_and_cost_center(
    session: Session,
) -> None:
    """Category and cost center filters select exact matches and combine."""
    account = _account(session)
    food = _category(session, key="category.food")
    other = _category(session, key="category.other")
    home = _cost_center(session, key="costcenter.household")
    car = _cost_center(session, key="costcenter.other")
    _transaction(
        session, account=account, description="a", category=food, cost_center=home
    )
    _transaction(
        session, account=account, description="b", category=food, cost_center=car
    )
    _transaction(
        session, account=account, description="c", category=other, cost_center=home
    )

    by_category = list_transactions(session, TransactionFilter(category_id=food.id))
    by_center = list_transactions(session, TransactionFilter(cost_center_id=home.id))
    both = list_transactions(
        session, TransactionFilter(category_id=food.id, cost_center_id=home.id)
    )

    assert {t.original_description for t in by_category} == {"a", "b"}
    assert {t.original_description for t in by_center} == {"a", "c"}
    assert [t.original_description for t in both] == ["a"]


def test_list_transactions_filters_by_nature(session: Session) -> None:
    """Fixed/variable derives from the category; unclassified rows never match."""
    account = _account(session)
    rent = _fixed_category(session, "category.rent")
    food = _category(session, key="category.food")
    _transaction(session, account=account, description="rent", category=rent)
    _transaction(session, account=account, description="food", category=food)
    _transaction(session, account=account, description="none")

    fixed = list_transactions(session, TransactionFilter(nature="fixed"))
    variable = list_transactions(session, TransactionFilter(nature="variable"))
    combined = list_transactions(
        session, TransactionFilter(nature="fixed", query="food")
    )

    assert [t.original_description for t in fixed] == ["rent"]
    assert [t.original_description for t in variable] == ["food"]
    assert combined == []
