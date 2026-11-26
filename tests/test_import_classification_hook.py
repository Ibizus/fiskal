"""Tests that classification runs right after an import."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fiskal.classification import RuleSpec, create_rule, validate_and_create_rule
from fiskal.classification.pairing_service import confirm_pair
from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import Account, Category, CostCenter, Rule, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    RuleOrigin,
    TransactionType,
)
from fiskal.importers import OpenbankImporter, import_statement, revert_import
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
        session.execute(
            select(Transaction)
            .filter_by(account_id=account.id)
            .order_by(Transaction.amount_minor)
        )
        .scalars()
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


def test_rule_hit_count_does_not_inflate_on_unrelated_rule_change(
    session: Session, tmp_path: Path
) -> None:
    """Reclassifying after an unrelated rule change does not recount existing hits.

    Trigger: a rule classifies pending rows on import and later rule CRUD calls
    ``reclassify_pending`` over the same rows.

    Visible symptom (pre-fix): the original rule's ``hit_count`` and
    ``last_hit_at`` increased again even though no new transaction arrived and
    the same rule still matched.
    """
    category = Category(
        name_key="category.food",
        kind="expense",
        nature="variable",
    )
    session.add(category)
    session.commit()

    rule = Rule(
        priority=1,
        mode="contains",
        pattern="grocery",
        transaction_type=TransactionType.EXPENSE.value,
        category_id=category.id,
        cost_center_id=None,
        origin=RuleOrigin.USER.value,
        is_active=True,
    )
    session.add(rule)
    session.commit()

    account = Account(
        name="Account",
        currency_code="EUR",
        default_cost_center_id=None,
    )
    session.add(account)
    session.commit()

    day = date(2024, 3, 15)
    movements = [_movement(day, "grocery store", -1000, 99_000)]
    path = tmp_path / "inflate.xls"
    path.write_bytes(_statement(path, movements))

    result = import_statement(
        session, OpenbankImporter(), account, "inflate.xls", path.read_bytes()
    )
    assert result.summary.classified_by_rule == 1

    session.refresh(rule)
    first_hit_count = rule.hit_count
    first_hit_at = rule.last_hit_at
    assert first_hit_count == 1
    assert first_hit_at is not None

    create_rule(
        session,
        RuleSpec(
            priority=2,
            mode="contains",
            pattern="unrelated",
            transaction_type="expense",
            category_id=category.id,
            cost_center_id=None,
            person_id=None,
            amount_min_minor=None,
            amount_max_minor=None,
            day_of_month_min=None,
            day_of_month_max=None,
            is_active=True,
        ),
    )

    session.refresh(rule)
    assert rule.hit_count == first_hit_count
    assert rule.last_hit_at == first_hit_at


def test_confirmed_internal_transfer_clears_rule_id(
    session: Session, tmp_path: Path
) -> None:
    """A confirmed internal-transfer pair loses any earlier rule reference.

    Trigger: an imported movement is classified by a rule before the user
    confirms it as one leg of an internal transfer.

    Visible symptom (pre-fix): after ``confirm_pair`` the leg had
    ``transaction_type == internal_transfer``, ``category_id == None`` and
    ``origin == manual``, but ``rule_id`` still pointed at the old rule.
    """
    category = Category(
        name_key="category.misc",
        kind="expense",
        nature="variable",
    )
    session.add(category)
    session.commit()

    rule = Rule(
        priority=1,
        mode="contains",
        pattern="cash withdrawal",
        transaction_type=TransactionType.EXPENSE.value,
        category_id=category.id,
        cost_center_id=None,
        origin=RuleOrigin.USER.value,
        is_active=True,
    )
    session.add(rule)
    session.commit()

    source = Account(name="Source", currency_code="EUR")
    target = Account(name="Target", currency_code="EUR")
    session.add_all([source, target])
    session.commit()

    day = date(2024, 3, 15)

    outgoing_movements = [_movement(day, "cash withdrawal", -5000, 95_000)]
    outgoing_path = tmp_path / "out.xls"
    outgoing_path.write_bytes(_statement(outgoing_path, outgoing_movements))
    import_statement(
        session,
        OpenbankImporter(),
        source,
        "out.xls",
        outgoing_path.read_bytes(),
    )

    incoming_movements = [_movement(day, "cash deposit", 5000, 105_000)]
    incoming_path = tmp_path / "in.xls"
    incoming_path.write_bytes(_statement(incoming_path, incoming_movements))
    import_statement(
        session,
        OpenbankImporter(),
        target,
        "in.xls",
        incoming_path.read_bytes(),
    )

    outgoing = session.execute(
        select(Transaction).filter_by(account_id=source.id)
    ).scalar_one()
    incoming = session.execute(
        select(Transaction).filter_by(account_id=target.id)
    ).scalar_one()
    assert outgoing.rule_id == rule.id
    assert outgoing.category_id == category.id

    confirm_pair(session, outgoing.id, incoming.id)

    session.expire_all()
    outgoing = session.execute(
        select(Transaction).filter_by(account_id=source.id)
    ).scalar_one()
    incoming = session.execute(
        select(Transaction).filter_by(account_id=target.id)
    ).scalar_one()
    assert outgoing.rule_id is None
    assert incoming.rule_id is None
    assert outgoing.transaction_type == TransactionType.INTERNAL_TRANSFER.value
    assert incoming.transaction_type == TransactionType.INTERNAL_TRANSFER.value


def test_default_cost_center_count_excludes_rule_classified_rows(
    session: Session, tmp_path: Path
) -> None:
    """Rows that got a category from a rule do not count as default-classified.

    Trigger: a rule sets a category but leaves the cost center unset, and the
    account has a default cost center.

    Visible symptom (pre-fix): the import summary reported the row in both
    ``classified_by_rule`` and ``classified_by_default`` because the default
    cost center filled the missing cost center.
    """
    default_cc = CostCenter(name_key="costcenter.personal")
    session.add(default_cc)
    session.commit()

    category = Category(
        name_key="category.food",
        kind="expense",
        nature="variable",
    )
    session.add(category)
    session.commit()

    rule = Rule(
        priority=1,
        mode="contains",
        pattern="grocery",
        transaction_type=TransactionType.EXPENSE.value,
        category_id=category.id,
        cost_center_id=None,
        origin=RuleOrigin.USER.value,
        is_active=True,
    )
    session.add(rule)
    session.commit()

    account = Account(
        name="Account",
        currency_code="EUR",
        default_cost_center_id=default_cc.id,
    )
    session.add(account)
    session.commit()

    day = date(2024, 3, 15)
    movements = [_movement(day, "grocery store", -1000, 99_000)]
    path = tmp_path / "default_count.xls"
    path.write_bytes(_statement(path, movements))

    result = import_statement(
        session, OpenbankImporter(), account, "default_count.xls", path.read_bytes()
    )

    assert result.summary.new_count == 1
    assert result.summary.classified_by_rule == 1
    assert result.summary.classified_by_history == 0
    assert result.summary.classified_by_default == 0
    assert result.summary.unclassified == 0

    txn = session.execute(select(Transaction)).scalar_one()
    assert txn.category_id == category.id
    assert txn.cost_center_id == default_cc.id
    assert txn.origin == ClassificationOrigin.RULE.value


def _setup_food_account(session: Session) -> tuple[Account, Category]:
    category = Category(name_key="category.food", kind="expense", nature="variable")
    account = Account(name="Account", currency_code="EUR")
    session.add_all([category, account])
    session.commit()
    return account, category


def test_import_validates_by_user_rule_and_reports_it(
    session: Session, tmp_path: Path
) -> None:
    """Rows a user rule matches arrive validated and are counted in the summary."""
    account, category = _setup_food_account(session)
    session.add(
        Rule(
            priority=1,
            mode="contains",
            pattern="grocery",
            transaction_type=TransactionType.EXPENSE.value,
            category_id=category.id,
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

    assert result.summary.classified_by_rule == 1
    assert result.summary.validated_by_rule == 1
    rows = {
        txn.normalized_description: txn
        for txn in session.execute(select(Transaction)).scalars()
    }
    assert rows["grocery store"].status == "validated"
    assert rows["grocery store"].validated_by_rule is True
    assert rows["cash withdrawal"].status == "pending"


def test_rule_from_imported_counterparty_matches_sibling_rows(
    session: Session, tmp_path: Path
) -> None:
    """A rule built from a card movement's counterparty matches the other card rows.

    Rules match the normalized description and counterparty, so card masks and
    trailing references that differ between rows must not matter.
    """
    account, category = _setup_food_account(session)
    day = date(2024, 3, 15)
    movements = [
        _movement(
            day,
            f"Purchase Demo Shop DEMO CARD 1234 56** **** 7890 REF {n:06d}",
            -1000 - n,
            99_000 - n,
        )
        for n in range(1, 4)
    ]
    path = tmp_path / "a.xls"
    path.write_bytes(_statement(path, movements))
    import_statement(session, OpenbankImporter(), account, "a.xls", path.read_bytes())
    rows = list(session.execute(select(Transaction)).scalars())
    assert {txn.counterparty for txn in rows} == {"Demo Shop"}

    first = rows[0]
    outcome = validate_and_create_rule(
        session,
        first.id,
        transaction_type="expense",
        category_id=category.id,
        cost_center_id=None,
        person_id=None,
    )

    assert outcome.validated_count == len(rows) - 1
    assert all(
        txn.status == "validated"
        for txn in session.execute(select(Transaction)).scalars()
    )


def test_import_with_rule_validated_rows_can_be_reverted(
    session: Session, tmp_path: Path
) -> None:
    """Rows a rule validated automatically do not block reverting the import."""
    account, category = _setup_food_account(session)
    session.add(
        Rule(
            priority=1,
            mode="contains",
            pattern="grocery",
            transaction_type=TransactionType.EXPENSE.value,
            category_id=category.id,
            origin=RuleOrigin.USER.value,
        )
    )
    session.commit()
    path = tmp_path / "a.xls"
    path.write_bytes(
        _statement(path, [_movement(date(2024, 3, 15), "grocery", -1000, 99_000)])
    )
    result = import_statement(
        session, OpenbankImporter(), account, "a.xls", path.read_bytes()
    )

    revert_import(session, result.import_id)

    assert session.execute(select(Transaction)).first() is None
