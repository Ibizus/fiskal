"""Tests that classification runs right after an import."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import Account, Category, CostCenter, Rule, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    RuleOrigin,
    TransactionType,
)
from fiskal.importers import OpenbankImporter, import_statement
from tests.support.openbank_synthetic import (
    GenerationOptions,
    Movement,
    build_statement_html,
)

_ACCOUNT_CODE = "12345678901234567890"


@pytest.fixture
def session() -> Session:
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _movement(day: date, description: str, amount: int, balance: int) -> Movement:
    return Movement(
        operation_date=day,
        value_date=day,
        description=description.ljust(100),
        amount_minor=amount,
        balance_minor=balance,
    )


def _statement(path: Path, movements: list[Movement]) -> bytes:
    options = GenerationOptions(
        movement_count=len(movements),
        start_date=movements[-1].operation_date,
        account_code=_ACCOUNT_CODE,
        opening_balance_minor=100_000,
        seed=42,
    )
    return build_statement_html(options, movements).encode("iso-8859-1")


def test_import_classifies_by_rule_and_default(
    session: Session, tmp_path: Path
) -> None:
    """Newly imported movements are classified right after import."""
    default_cc = CostCenter(name_key="costcenter.personal")
    session.add(default_cc)
    session.commit()
    rule_cc = CostCenter(name_key="costcenter.household")
    session.add(rule_cc)
    session.commit()
    account = Account(
        name="Account",
        currency_code="EUR",
        default_cost_center_id=default_cc.id,
    )
    session.add(account)
    session.commit()
    category = Category(
        name_key="category.food",
        kind="expense",
        nature="variable",
    )
    session.add(category)
    session.commit()
    session.add(
        Rule(
            priority=1,
            mode="contains",
            pattern="grocery",
            transaction_type=TransactionType.EXPENSE.value,
            category_id=category.id,
            cost_center_id=rule_cc.id,
            origin=RuleOrigin.USER.value,
        )
    )
    session.commit()

    day = date(2024, 3, 15)
    movements = [
        _movement(day, "grocery store", -1000, 99_000),
        _movement(day, "cash withdrawal", -500, 98_500),
    ]
    path = tmp_path / "a.xls"
    path.write_bytes(_statement(path, movements))

    result = import_statement(
        session, OpenbankImporter(), account, "a.xls", path.read_bytes()
    )

    assert result.summary.new_count == len(movements)
    assert result.summary.classified_by_rule == 1
    assert result.summary.classified_by_history == 0
    assert result.summary.classified_by_default == 1
    assert result.summary.unclassified == 1

    transactions = (
        session.query(Transaction)
        .filter_by(account_id=account.id)
        .order_by(Transaction.amount_minor)
        .all()
    )
    by_normalized = {txn.normalized_description: txn for txn in transactions}
    classified = by_normalized["grocery store"]
    unclassified = by_normalized["cash withdrawal"]

    assert classified.category_id == category.id
    assert classified.cost_center_id == rule_cc.id
    assert classified.origin == ClassificationOrigin.RULE.value
    assert classified.rule_id is not None
    assert unclassified.category_id is None
    assert unclassified.cost_center_id == default_cc.id
    assert unclassified.origin == ClassificationOrigin.MANUAL.value
