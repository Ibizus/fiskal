"""Tests for neutral seed data installation."""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.classification import (
    RuleSpec,
    classify_pending,
    delete_rule,
    seed_neutral_data,
    update_rule,
)
from fiskal.db.models import Category, Rule
from fiskal.domain.enums import ClassificationOrigin, RuleOrigin, TransactionType
from fiskal.domain.seed import DEFAULT_CATEGORIES, DEFAULT_COST_CENTERS, DEFAULT_RULES
from tests.support.factories import _account, _category, _rule, _transaction


def test_seed_neutral_data_is_idempotent(session: Session) -> None:
    """Seeding inserts defaults once and changes nothing on a re-run."""
    first = seed_neutral_data(session)
    assert first.categories_created == len(DEFAULT_CATEGORIES)
    assert first.cost_centers_created == len(DEFAULT_COST_CENTERS)
    assert first.rules_created == len(DEFAULT_RULES)

    second = seed_neutral_data(session)
    assert second.categories_created == 0
    assert second.cost_centers_created == 0
    assert second.rules_created == 0

    rules = session.query(Rule).filter_by(origin=RuleOrigin.SEED.value).all()
    assert len(rules) == len(DEFAULT_RULES)


def test_seed_does_not_resurrect_deleted_seed_rule(session: Session) -> None:
    """Deleting a seed rule and reseeding does not bring it back."""
    seed_neutral_data(session)
    salary = session.query(Rule).filter_by(pattern="salary").one()
    delete_rule(session, salary.id)

    result = seed_neutral_data(session)

    assert result.rules_created == 0
    assert session.query(Rule).filter_by(pattern="salary").count() == 0
    assert session.query(Rule).count() == len(DEFAULT_RULES) - 1


def test_seed_does_not_duplicate_edited_seed_rule(session: Session) -> None:
    """An edited seed rule is not re-added next to the edited copy."""
    seed_neutral_data(session)
    salary = session.query(Rule).filter_by(pattern="salary").one()
    update_rule(
        session,
        salary.id,
        RuleSpec(
            priority=salary.priority,
            mode=salary.mode,
            pattern="monthly wage",
            transaction_type=salary.transaction_type,
            category_id=salary.category_id,
            amount_min_minor=1,
            cost_center_id=None,
            person_id=None,
            amount_max_minor=None,
            day_of_month_min=None,
            day_of_month_max=None,
            is_active=True,
        ),
    )

    result = seed_neutral_data(session)

    assert result.rules_created == 0
    assert session.query(Rule).filter_by(pattern="salary").count() == 0
    assert session.query(Rule).count() == len(DEFAULT_RULES)


def test_seed_skips_rules_when_any_rule_exists(session: Session) -> None:
    """A profile with an existing (even inactive) rule gets no seed rules."""
    category = _category(session)
    _rule(session, category=category, is_active=False)

    result = seed_neutral_data(session)

    assert result.rules_created == 0
    assert result.categories_created == len(DEFAULT_CATEGORIES) - 1
    assert session.query(Rule).count() == 1


def test_seed_neutral_data_never_overwrites_custom_names(session: Session) -> None:
    """A customised category name survives a second seeding run."""
    seed_neutral_data(session)
    category = session.query(Category).filter_by(name_key="category.food").one()
    category.custom_name = "Groceries"
    session.commit()

    seed_neutral_data(session)

    session.refresh(category)
    assert category.custom_name == "Groceries"


def test_seed_income_rules_do_not_match_outgoing_payments(session: Session) -> None:
    """An outgoing payment mentioning salary is not classified as income."""
    seed_neutral_data(session)
    account = _account(session)
    outgoing = _transaction(
        session, account=account, description="salary payment", amount_minor=-2000
    )
    payroll_fee = _transaction(
        session, account=account, description="payroll service", amount_minor=-300
    )
    incoming = _transaction(
        session, account=account, description="monthly salary", amount_minor=2000
    )

    classify_pending(session)

    for txn in (outgoing, payroll_fee, incoming):
        session.refresh(txn)
    assert outgoing.category_id is None
    assert outgoing.transaction_type == TransactionType.EXPENSE.value
    assert payroll_fee.category_id is None
    assert incoming.transaction_type == TransactionType.INCOME.value
    assert incoming.origin == ClassificationOrigin.RULE.value


def test_seed_splits_income_categories_with_sources(session: Session) -> None:
    """The neutral seed installs work, benefit and rental income categories."""
    seed_neutral_data(session)

    income_categories = (
        session.query(Category)
        .filter(Category.kind == "income")
        .order_by(Category.name_key)
        .all()
    )
    by_key = {category.name_key: category for category in income_categories}

    assert set(by_key) == {
        "category.income.work",
        "category.income.benefit",
        "category.income.rental",
    }
    assert by_key["category.income.work"].income_source == "work"
    assert by_key["category.income.benefit"].income_source == "benefit"
    assert by_key["category.income.rental"].income_source == "rental"


def test_seed_income_rules_point_at_work_income(session: Session) -> None:
    """Salary and payroll seed rules classify to the work income category."""
    seed_neutral_data(session)
    account = _account(session)
    txn = _transaction(
        session, account=account, description="monthly salary", amount_minor=2000
    )

    classify_pending(session)

    session.refresh(txn)
    work = session.query(Category).filter_by(name_key="category.income.work").one()
    assert txn.category_id == work.id


def test_seed_withdrawal_rule_does_not_match_positive_reversal(
    session: Session,
) -> None:
    """A positive cash withdrawal reversal is not classified as a withdrawal."""
    seed_neutral_data(session)
    account = _account(session)
    reversal = _transaction(
        session, account=account, description="cash withdrawal", amount_minor=4000
    )
    withdrawal = _transaction(
        session, account=account, description="cash withdrawal", amount_minor=-4000
    )

    classify_pending(session)

    session.refresh(reversal)
    session.refresh(withdrawal)
    assert reversal.category_id is None
    assert reversal.transaction_type == TransactionType.INCOME.value
    assert withdrawal.origin == ClassificationOrigin.RULE.value
    assert withdrawal.transaction_type == TransactionType.EXPENSE.value
