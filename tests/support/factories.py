"""Synthetic factories for classification engine tests.

All data is synthetic; no personal or bank-specific information is used.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from fiskal.db.models import Account, Category, CostCenter, Person, Rule, Transaction
from fiskal.domain.enums import (
    CategoryKind,
    CategoryNature,
    ClassificationOrigin,
    RuleOrigin,
    TransactionStatus,
    TransactionType,
)


def _person(session: Session, *, name: str = "Ada") -> Person:
    person = Person(name=name)
    session.add(person)
    session.commit()
    return person


def _account(
    session: Session, *, name: str = "Account", default_cc: CostCenter | None = None
) -> Account:
    account = Account(
        name=name,
        currency_code="EUR",
        default_cost_center_id=default_cc.id if default_cc else None,
    )
    session.add(account)
    session.commit()
    return account


def _category(
    session: Session,
    *,
    key: str = "category.food",
    kind: CategoryKind = CategoryKind.EXPENSE,
    income_source: str | None = None,
) -> Category:
    category = Category(
        name_key=key,
        kind=kind.value,
        nature=CategoryNature.VARIABLE.value,
        income_source=income_source,
    )
    session.add(category)
    session.commit()
    return category


def _cost_center(session: Session, *, key: str = "costcenter.household") -> CostCenter:
    cost_center = CostCenter(name_key=key)
    session.add(cost_center)
    session.commit()
    return cost_center


def _rule(
    session: Session,
    *,
    priority: int = 1,
    pattern: str = "grocery",
    mode: str = "contains",
    transaction_type: str = "expense",
    category: Category,
    cost_center: CostCenter | None = None,
    person: Person | None = None,
    origin: RuleOrigin = RuleOrigin.USER,
    is_active: bool = True,
) -> Rule:
    rule = Rule(
        priority=priority,
        mode=mode,
        pattern=pattern,
        transaction_type=transaction_type,
        category_id=category.id,
        cost_center_id=cost_center.id if cost_center else None,
        person_id=person.id if person else None,
        origin=origin.value,
        is_active=is_active,
    )
    session.add(rule)
    session.commit()
    return rule


def _transaction(
    session: Session,
    *,
    account: Account,
    description: str = "grocery store",
    counterparty: str | None = "Fresh Market",
    amount_minor: int = -1000,
    day: int = 15,
    status: TransactionStatus = TransactionStatus.PENDING,
    origin: ClassificationOrigin = ClassificationOrigin.MANUAL,
    category: Category | None = None,
    cost_center: CostCenter | None = None,
    person: Person | None = None,
    transaction_type: TransactionType | None = None,
    identity: str | None = None,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        operation_date=date(2024, 1, day),
        value_date=date(2024, 1, day),
        original_description=description,
        normalized_description=description,
        counterparty=counterparty,
        amount_minor=amount_minor,
        balance_minor=0,
        currency_code="EUR",
        transaction_type=(
            transaction_type.value
            if transaction_type
            else (
                TransactionType.INCOME.value
                if amount_minor >= 0
                else TransactionType.EXPENSE.value
            )
        ),
        cost_center_id=cost_center.id if cost_center else None,
        category_id=category.id if category else None,
        person_id=person.id if person else None,
        origin=origin.value,
        status=status.value,
        identity_key=identity or str(uuid4()),
    )
    session.add(txn)
    session.commit()
    return txn
