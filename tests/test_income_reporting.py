"""Tests for the income report helpers.

All data is synthetic; no personal or bank-specific information is used.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.domain.enums import CategoryKind, TransactionType
from fiskal.reports.income import (
    earned_income_minor,
    income_by_holder,
    labor_income_minor,
    other_inflows_minor,
    rental_income_minor,
    total_income,
    unattributed_income,
)
from tests.support.factories import (
    _account,
    _category,
    _person,
    _transaction,
)


def _income_category(session: Session, *, key: str, income_source: str | None):
    return _category(
        session, key=key, kind=CategoryKind.INCOME, income_source=income_source
    )


def test_income_by_holder_groups_attributed_income(session: Session) -> None:
    """Attributed income is grouped per holder with the source split."""
    account = _account(session)
    work = _income_category(session, key="category.work", income_source="work")
    benefit = _income_category(session, key="category.benefit", income_source="benefit")
    rental = _income_category(session, key="category.rental", income_source="rental")
    other = _income_category(session, key="category.other", income_source="other")
    ada = _person(session, name="Ada")
    grace = _person(session, name="Grace")

    salary = 100_000
    bonus = 20_000
    allowance = 50_000
    rent = 30_000
    gift = 2_000
    _transaction(
        session, account=account, category=work, person=ada, amount_minor=salary
    )
    _transaction(
        session, account=account, category=work, person=ada, amount_minor=bonus
    )
    _transaction(
        session, account=account, category=benefit, person=grace, amount_minor=allowance
    )
    _transaction(
        session, account=account, category=rental, person=ada, amount_minor=rent
    )
    _transaction(
        session, account=account, category=other, person=grace, amount_minor=gift
    )

    holders = {holder.person_name: holder for holder in income_by_holder(session)}

    ada_labor = salary + bonus
    assert set(holders) == {"Ada", "Grace"}
    assert holders["Ada"].breakdown.labor_minor == ada_labor
    assert holders["Ada"].breakdown.rental_minor == rent
    assert holders["Ada"].breakdown.other_minor == 0
    assert holders["Ada"].breakdown.earned_minor == ada_labor + rent
    assert holders["Grace"].breakdown.labor_minor == allowance
    assert holders["Grace"].breakdown.other_minor == gift
    assert holders["Grace"].breakdown.earned_minor == allowance


def test_unattributed_and_total_income(session: Session) -> None:
    """Unattributed income is reported separately from the attributed total."""
    account = _account(session)
    work = _income_category(session, key="category.work", income_source="work")
    rental = _income_category(session, key="category.rental", income_source="rental")
    other = _income_category(session, key="category.other", income_source="other")
    ada = _person(session, name="Ada")

    salary = 100_000
    shared_rent = 10_000
    gift = 5_000
    _transaction(
        session, account=account, category=work, person=ada, amount_minor=salary
    )
    _transaction(session, account=account, category=rental, amount_minor=shared_rent)
    _transaction(session, account=account, category=other, amount_minor=gift)

    unattributed = unattributed_income(session)
    assert unattributed.labor_minor == 0
    assert unattributed.rental_minor == shared_rent
    assert unattributed.other_minor == gift
    assert unattributed.earned_minor == shared_rent

    total = total_income(session)
    assert total.labor_minor == salary
    assert total.rental_minor == shared_rent
    assert total.other_minor == gift
    assert total.earned_minor == salary + shared_rent
    assert total.total_minor == salary + shared_rent + gift


def test_scalar_helpers_match_the_breakdown(session: Session) -> None:
    """The scalar helpers return the matching breakdown fields."""
    account = _account(session)
    work = _income_category(session, key="category.work", income_source="work")
    benefit = _income_category(session, key="category.benefit", income_source="benefit")
    rental = _income_category(session, key="category.rental", income_source="rental")
    other = _income_category(session, key="category.other", income_source="other")

    wages = 70_000
    allowance = 30_000
    rent = 20_000
    gift = 7_000
    _transaction(session, account=account, category=work, amount_minor=wages)
    _transaction(session, account=account, category=benefit, amount_minor=allowance)
    _transaction(session, account=account, category=rental, amount_minor=rent)
    _transaction(session, account=account, category=other, amount_minor=gift)

    assert labor_income_minor(session) == wages + allowance
    assert rental_income_minor(session) == rent
    assert earned_income_minor(session) == wages + allowance + rent
    assert other_inflows_minor(session) == gift


def test_income_without_a_source_counts_as_other(session: Session) -> None:
    """Income whose category has no source is treated as other inflows."""
    account = _account(session)
    unsourced = _income_category(session, key="category.generic", income_source=None)

    amount = 4_000
    _transaction(session, account=account, category=unsourced, amount_minor=amount)

    breakdown = total_income(session)
    assert breakdown.labor_minor == 0
    assert breakdown.rental_minor == 0
    assert breakdown.other_minor == amount
    assert breakdown.earned_minor == 0


def test_uncategorized_income_counts_as_other(session: Session) -> None:
    """An unclassified pending inflow counts as other income everywhere."""
    account = _account(session)
    ada = _person(session, name="Ada")

    unattributed_amount = 3_000
    attributed_amount = 1_500
    _transaction(session, account=account, amount_minor=unattributed_amount)
    _transaction(session, account=account, person=ada, amount_minor=attributed_amount)

    total = total_income(session)
    assert total.other_minor == unattributed_amount + attributed_amount
    assert total.earned_minor == 0
    assert unattributed_income(session).other_minor == unattributed_amount
    holders = income_by_holder(session)
    assert [holder.person_name for holder in holders] == ["Ada"]
    assert holders[0].breakdown.other_minor == attributed_amount
    assert holders[0].breakdown.earned_minor == 0


def test_refunds_and_transfers_are_not_income(session: Session) -> None:
    """Refunds, savings and internal transfers never count as income."""
    account = _account(session)
    work = _income_category(session, key="category.work", income_source="work")

    _transaction(
        session,
        account=account,
        category=work,
        amount_minor=1_000,
        transaction_type=TransactionType.REFUND,
    )
    _transaction(
        session,
        account=account,
        category=work,
        amount_minor=2_000,
        transaction_type=TransactionType.SAVINGS_INVESTMENT,
    )
    _transaction(
        session,
        account=account,
        category=work,
        amount_minor=3_000,
        transaction_type=TransactionType.INTERNAL_TRANSFER,
    )

    assert total_income(session).total_minor == 0
    assert income_by_holder(session) == []
    assert unattributed_income(session).total_minor == 0
