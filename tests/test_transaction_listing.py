"""Tests for the filtered transaction listing service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.domain.enums import TransactionStatus, TransactionType
from fiskal.reports.helpers import TransactionFilter, list_transactions
from tests.support.factories import _account, _category, _transaction


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
