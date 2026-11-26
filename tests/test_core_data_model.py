"""Tests for the v1 core data model.

All data is synthetic; no personal or bank-specific information is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import (
    Account,
    AccountHolder,
    Category,
    CostCenter,
    Import,
    Person,
    RejectedPair,
    Rule,
    Transaction,
)
from fiskal.domain.enums import (
    CategoryKind,
    CategoryNature,
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)


def _in_memory_session() -> Session:
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_account(session: Session, *, name: str = "Test account") -> Account:
    account = Account(name=name, currency_code="EUR")
    session.add(account)
    session.commit()
    return account


def _make_category(
    session: Session,
    *,
    name_key: str = "category.misc",
    kind: CategoryKind = CategoryKind.EXPENSE,
    nature: CategoryNature = CategoryNature.VARIABLE,
) -> Category:
    category = Category(
        name_key=name_key,
        kind=kind.value,
        nature=nature.value,
    )
    session.add(category)
    session.commit()
    return category


def _make_cost_center(
    session: Session,
    *,
    name_key: str = "costcenter.personal",
) -> CostCenter:
    cost_center = CostCenter(name_key=name_key)
    session.add(cost_center)
    session.commit()
    return cost_center


def _make_import(session: Session, account: Account) -> Import:
    imp = Import(
        account_id=account.id,
        file_name="synthetic.xls",
        file_hash="a" * 64,
    )
    session.add(imp)
    session.commit()
    return imp


def _make_transaction(
    session: Session,
    *,
    account: Account,
    identity_key: str,
    amount_minor: int = 1000,
    balance_minor: int | None = 5000,
    transaction_type: TransactionType | str = TransactionType.EXPENSE,
    status: TransactionStatus | str = TransactionStatus.PENDING,
    origin: ClassificationOrigin | str = ClassificationOrigin.MANUAL,
    category: Category | None = None,
    cost_center: CostCenter | None = None,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        operation_date=date(2024, 1, 15),
        value_date=date(2024, 1, 15),
        original_description="SYNTHETIC DESCRIPTION",
        normalized_description="synthetic description",
        counterparty="Synthetic counterparty",
        amount_minor=amount_minor,
        balance_minor=balance_minor,
        currency_code=account.currency_code,
        transaction_type=(
            transaction_type
            if isinstance(transaction_type, str)
            else transaction_type.value
        ),
        origin=origin if isinstance(origin, str) else origin.value,
        status=status if isinstance(status, str) else status.value,
        identity_key=identity_key,
        category_id=category.id if category else None,
        cost_center_id=cost_center.id if cost_center else None,
    )
    session.add(txn)
    session.commit()
    return txn


def test_money_is_stored_as_exact_integers() -> None:
    """Amount and balance use integer minor units, never floats or decimals."""
    session = _in_memory_session()
    account = _make_account(session)
    category = _make_category(session)

    txn = _make_transaction(
        session,
        account=account,
        category=category,
        amount_minor=12_345,
        balance_minor=99_900,
        identity_key="money-int",
    )

    expected_amount = 12_345
    expected_balance = 99_900
    assert isinstance(txn.amount_minor, int)
    assert txn.amount_minor == expected_amount
    assert txn.balance_minor == expected_balance
    session.close()


def test_identity_key_unique_per_account() -> None:
    """The same identity key may exist in two accounts, but not twice in one."""
    session = _in_memory_session()
    account_a = _make_account(session, name="Account A")
    account_b = _make_account(session, name="Account B")
    category = _make_category(session)

    _make_transaction(
        session,
        account=account_a,
        category=category,
        identity_key="shared-id",
    )
    _make_transaction(
        session,
        account=account_b,
        category=category,
        identity_key="shared-id",
    )

    with pytest.raises(IntegrityError):
        _make_transaction(
            session,
            account=account_a,
            category=category,
            identity_key="shared-id",
        )
    session.close()


def test_cost_center_requires_a_name() -> None:
    """A cost center must have at least a translation key or a custom name."""
    session = _in_memory_session()

    with pytest.raises(IntegrityError):
        session.add(CostCenter())
        session.commit()

    session.rollback()
    _make_cost_center(session, name_key="costcenter.work")
    session.add(CostCenter(custom_name="Custom"))
    session.commit()
    session.close()


def test_category_requires_a_name() -> None:
    """A category must have at least a translation key or a custom name."""
    session = _in_memory_session()

    with pytest.raises(IntegrityError):
        session.add(
            Category(
                kind=CategoryKind.EXPENSE.value,
                nature=CategoryNature.FIXED.value,
            ),
        )
        session.commit()

    session.rollback()
    _make_category(session, name_key="category.food")
    session.add(
        Category(
            custom_name="Custom",
            kind=CategoryKind.INCOME.value,
            nature=CategoryNature.FIXED.value,
        ),
    )
    session.commit()
    session.close()


def test_invalid_transaction_type_is_rejected() -> None:
    """Only the five domain transaction types may be stored."""
    session = _in_memory_session()
    account = _make_account(session)

    with pytest.raises(IntegrityError):
        _make_transaction(
            session,
            account=account,
            identity_key="bad-type",
            transaction_type="not-a-type",
        )
    session.close()


def test_invalid_transaction_status_is_rejected() -> None:
    """Only pending and validated statuses may be stored."""
    session = _in_memory_session()
    account = _make_account(session)

    with pytest.raises(IntegrityError):
        _make_transaction(
            session,
            account=account,
            identity_key="bad-status",
            status="unknown",
        )
    session.close()


def test_foreign_keys_are_enforced() -> None:
    """SQLite foreign-key support is active and rejects dangling references."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    with pytest.raises(IntegrityError):
        session.add(
            Transaction(
                account_id=uuid4(),
                operation_date=date(2024, 1, 15),
                value_date=date(2024, 1, 15),
                original_description="x",
                normalized_description="x",
                amount_minor=100,
                currency_code="EUR",
                transaction_type=TransactionType.EXPENSE.value,
                origin=ClassificationOrigin.MANUAL.value,
                status=TransactionStatus.PENDING.value,
                identity_key="orphan",
            ),
        )
        session.commit()

    session.close()


def test_plain_engine_does_not_enable_foreign_keys_by_default() -> None:
    """``create_engine`` alone leaves the pragma off; the helper turns it on."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        result = connection.exec_driver_sql("PRAGMA foreign_keys")
        assert result.scalar() == 0


def test_make_engine_enables_foreign_keys() -> None:
    """The project helper enables foreign-key enforcement."""
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        result = connection.exec_driver_sql("PRAGMA foreign_keys")
        assert result.scalar() == 1


def _run_alembic(
    db_path: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    alembic_ini = Path(__file__).resolve().parents[1] / "migrations" / "alembic.ini"
    env = {
        **dict(os.environ),
        "FISKAL_MIGRATION_URL": f"sqlite:///{db_path}",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(alembic_ini),
            *args,
        ],
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def test_migration_upgrade_downgrade_round_trip(tmp_path: Path) -> None:
    """The new migration can be applied, reverted, and reapplied cleanly."""
    db_path = tmp_path / "migration.db"

    _run_alembic(db_path, "upgrade", "head")
    _run_alembic(db_path, "downgrade", "-1")
    _run_alembic(db_path, "upgrade", "head")
    _run_alembic(db_path, "check")


def _table_names(db_path: Path) -> set[str]:
    with make_engine(f"sqlite:///{db_path}").connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        return {row[0] for row in rows}


def _seed_transaction(session: Session) -> None:
    _make_transaction(session, account=_make_account(session), identity_key="k1")


def _seed_import(session: Session) -> None:
    _make_import(session, _make_account(session))


def _seed_paired_transactions(session: Session) -> None:
    account = _make_account(session)
    outgoing = _make_transaction(
        session, account=account, identity_key="pair-out", amount_minor=-10_000
    )
    incoming_account = _make_account(session, name="Other account")
    incoming = _make_transaction(
        session,
        account=incoming_account,
        identity_key="pair-in",
        amount_minor=10_000,
    )
    outgoing.paired_transaction_id = incoming.id
    incoming.paired_transaction_id = outgoing.id
    session.commit()


def _seed_rejected_pair(session: Session) -> None:
    account = _make_account(session)
    left = _make_transaction(session, account=account, identity_key="rej-left")
    right = _make_transaction(
        session,
        account=_make_account(session, name="Other account"),
        identity_key="rej-right",
    )
    ordered = (
        (left.id, right.id) if str(left.id) <= str(right.id) else (right.id, left.id)
    )
    session.add(
        RejectedPair(left_transaction_id=ordered[0], right_transaction_id=ordered[1])
    )
    session.commit()


def _seed_person(session: Session) -> None:
    session.add(Person(name="Holder"))
    session.commit()


def _seed_account_holder(session: Session) -> None:
    account = _make_account(session)
    person = Person(name="Holder")
    session.add(person)
    session.commit()
    session.add(AccountHolder(account_id=account.id, person_id=person.id))
    session.commit()


def _seed_attributed_transaction(session: Session) -> None:
    account = _make_account(session)
    person = Person(name="Holder")
    session.add(person)
    session.commit()
    txn = _make_transaction(session, account=account, identity_key="attributed")
    txn.person_id = person.id
    session.commit()


def _seed_person_rule(session: Session) -> None:
    category = _make_category(session)
    person = Person(name="Holder")
    session.add(person)
    session.commit()
    session.add(
        Rule(
            priority=1,
            mode="contains",
            pattern="test",
            transaction_type="expense",
            category_id=category.id,
            origin="user",
            person_id=person.id,
        )
    )
    session.commit()


def _seed_income_source_category(session: Session) -> None:
    session.add(
        Category(
            name_key="category.income.work",
            kind="income",
            nature="variable",
            income_source="work",
        )
    )
    session.commit()


@pytest.mark.parametrize(
    "seed",
    [
        _seed_paired_transactions,
        _seed_rejected_pair,
    ],
)
def test_downgrade_with_pairing_data_is_refused(tmp_path: Path, seed: Any) -> None:
    """Dropping the person column rebuilds transactions, so pairing data blocks it."""
    db_path = tmp_path / "populated.db"
    _run_alembic(db_path, "upgrade", "head")
    tables_before = _table_names(db_path)
    session = sessionmaker(bind=make_engine(f"sqlite:///{db_path}"))()
    seed(session)
    session.close()
    _run_alembic(db_path, "downgrade", "a3f8c2e6b1d4")
    tables_before = _table_names(db_path)

    result = _run_alembic(db_path, "downgrade", "-1", check=False)

    assert result.returncode != 0
    assert _table_names(db_path) == tables_before
    current = _run_alembic(db_path, "current")
    assert "a3f8c2e6b1d4" in current.stdout


def _raw_pairing_seed(db_path: Path, *, kind: str) -> None:
    """Seed pairing data with raw SQL so the dropped rule_id column is absent."""
    engine = make_engine(f"sqlite:///{db_path}")
    account_a = uuid4().hex
    account_b = uuid4().hex
    txn_a = uuid4().hex
    txn_b = uuid4().hex
    with engine.begin() as connection:
        for account_id in (account_a, account_b):
            connection.exec_driver_sql(
                "INSERT INTO accounts (id, name, currency_code, is_active) "
                "VALUES (?, 'Account', 'EUR', 1)",
                (account_id,),
            )
        for txn_id, account_id, amount, identity in (
            (txn_a, account_a, -10000, "pair-out"),
            (txn_b, account_b, 10000, "pair-in"),
        ):
            connection.exec_driver_sql(
                "INSERT INTO transactions (id, account_id, operation_date, "
                "value_date, original_description, normalized_description, "
                "amount_minor, balance_minor, currency_code, type, origin, "
                "status, is_manual, identity_key) VALUES (?, ?, '2024-01-15', "
                "'2024-01-15', 'x', 'x', ?, 0, 'EUR', 'expense', 'manual', "
                "'pending', 0, ?)",
                (txn_id, account_id, amount, identity),
            )
        if kind == "paired":
            connection.exec_driver_sql(
                "UPDATE transactions SET paired_transaction_id = ? WHERE id = ?",
                (txn_b, txn_a),
            )
            connection.exec_driver_sql(
                "UPDATE transactions SET paired_transaction_id = ? WHERE id = ?",
                (txn_a, txn_b),
            )
        else:
            left, right = (txn_a, txn_b) if txn_a <= txn_b else (txn_b, txn_a)
            connection.exec_driver_sql(
                "INSERT INTO rejected_pairs "
                "(left_transaction_id, right_transaction_id, rejected_at) "
                "VALUES (?, ?, '2024-01-15T00:00:00')",
                (left, right),
            )


@pytest.mark.parametrize("kind", ["paired", "rejected"])
def test_pairing_downgrade_with_pairing_data_is_refused(
    tmp_path: Path, kind: str
) -> None:
    """The pairing migration's own downgrade guard refuses pairing data."""
    db_path = tmp_path / "populated.db"
    _run_alembic(db_path, "upgrade", "head")
    _run_alembic(db_path, "downgrade", "694949b009bf")
    _raw_pairing_seed(db_path, kind=kind)
    tables_before = _table_names(db_path)

    result = _run_alembic(db_path, "downgrade", "-1", check=False)

    assert result.returncode != 0
    assert _table_names(db_path) == tables_before
    current = _run_alembic(db_path, "current")
    assert "694949b009bf" in current.stdout


def _raw_rule_seed(db_path: Path) -> None:
    """Seed a rule with raw SQL so the absent holders columns stay out of it."""
    engine = make_engine(f"sqlite:///{db_path}")
    category_id = uuid4().hex
    rule_id = uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO categories (id, name_key, custom_name, kind, nature, "
            "default_proration_months, is_active) VALUES (?, 'category.misc', "
            "NULL, 'expense', 'variable', NULL, 1)",
            (category_id,),
        )
        connection.exec_driver_sql(
            "INSERT INTO rules (id, priority, mode, pattern, amount_min_minor, "
            "amount_max_minor, day_of_month_min, day_of_month_max, "
            "transaction_type, cost_center_id, category_id, is_active, origin, "
            "hit_count, last_hit_at) VALUES (?, 1, 'contains', 'test', NULL, "
            "NULL, NULL, NULL, 'expense', NULL, ?, 1, 'user', 0, NULL)",
            (rule_id, category_id),
        )


def test_rules_downgrade_with_rules_is_refused(tmp_path: Path) -> None:
    """Downgrading the rules table on a populated database is refused."""
    db_path = tmp_path / "populated.db"
    _run_alembic(db_path, "upgrade", "head")
    _run_alembic(db_path, "downgrade", "dfcac1b70385")
    _raw_rule_seed(db_path)
    tables_before = _table_names(db_path)

    result = _run_alembic(db_path, "downgrade", "-1", check=False)

    assert result.returncode != 0
    assert "rules" in result.stderr
    assert _table_names(db_path) == tables_before
    current = _run_alembic(db_path, "current")
    assert "dfcac1b70385" in current.stdout


@pytest.mark.parametrize(
    "seed",
    [
        _seed_person,
        _seed_account_holder,
        _seed_attributed_transaction,
        _seed_person_rule,
        _seed_income_source_category,
    ],
)
def test_holders_downgrade_with_data_is_refused(tmp_path: Path, seed: Any) -> None:
    """Downgrading the holders migration on a populated database is refused."""
    db_path = tmp_path / "populated.db"
    _run_alembic(db_path, "upgrade", "head")
    tables_before = _table_names(db_path)
    session = sessionmaker(bind=make_engine(f"sqlite:///{db_path}"))()
    seed(session)
    session.close()
    _run_alembic(db_path, "downgrade", "a3f8c2e6b1d4")
    tables_before = _table_names(db_path)

    result = _run_alembic(db_path, "downgrade", "-1", check=False)

    assert result.returncode != 0
    assert _table_names(db_path) == tables_before
    current = _run_alembic(db_path, "current")
    assert "a3f8c2e6b1d4" in current.stdout


@pytest.mark.parametrize(
    "seed",
    [
        _seed_transaction,
        _seed_import,
        _make_account,
        _make_category,
        _make_cost_center,
    ],
)
def test_core_downgrade_with_rows_is_refused(tmp_path: Path, seed: Any) -> None:
    """Downgrading the core data model on a populated database is refused."""
    db_path = tmp_path / "populated.db"
    _run_alembic(db_path, "upgrade", "head")
    session = sessionmaker(bind=make_engine(f"sqlite:///{db_path}"))()
    seed(session)
    session.close()
    _run_alembic(db_path, "downgrade", "01d213a4aac2")
    tables_before = _table_names(db_path)

    result = _run_alembic(db_path, "downgrade", "c82da8d3d961", check=False)

    assert result.returncode != 0
    assert "empty database" in result.stderr
    assert _table_names(db_path) == tables_before
    current = _run_alembic(db_path, "current")
    assert "01d213a4aac2" in current.stdout


def test_downgrade_on_empty_database_restores_placeholder(tmp_path: Path) -> None:
    """An empty database downgrades through every revision to the placeholder."""
    db_path = tmp_path / "empty.db"
    _run_alembic(db_path, "upgrade", "head")

    _run_alembic(db_path, "downgrade", "c82da8d3d961")

    assert _table_names(db_path) == {"alembic_version", "transactions"}
    with make_engine(f"sqlite:///{db_path}").connect() as connection:
        columns = {
            row[1]: (row[2], bool(row[3]))
            for row in connection.exec_driver_sql("PRAGMA table_info(transactions)")
        }
        version = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar()
    assert columns == {
        "id": ("CHAR(32)", True),
        "amount": ("NUMERIC(14, 4)", True),
        "currency": ("VARCHAR(3)", True),
        "description": ("VARCHAR(255)", True),
    }
    assert version == "c82da8d3d961"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_type", "not-a-type"),
        ("status", "unknown"),
        ("origin", "not-an-origin"),
    ],
)
def test_migrated_database_rejects_invalid_transaction_values(
    tmp_path: Path, field: str, value: str
) -> None:
    """A database built by the migrations enforces the transaction checks."""
    db_path = tmp_path / "migrated.db"
    _run_alembic(db_path, "upgrade", "head")
    session = sessionmaker(bind=make_engine(f"sqlite:///{db_path}"))()
    account = _make_account(session)

    with pytest.raises(IntegrityError):
        _make_transaction(
            session,
            account=account,
            identity_key=f"bad-{field}",
            **{field: value},
        )
    session.close()


def test_upgrade_populated_profile_from_previous_revision(tmp_path: Path) -> None:
    """A seeded profile with rules and transactions upgrades to head.

    Rebuilding ``categories`` while rows reference it must not trip SQLite's
    foreign-key enforcement and leave the profile half-migrated.
    """
    db_path = tmp_path / "existing.db"
    _run_alembic(db_path, "upgrade", "dfcac1b70385")
    account_id, category_id, rule_id, txn_id = (uuid4().hex for _ in range(4))
    engine = make_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO accounts (id, name, currency_code, is_active) "
            "VALUES (?, 'Account', 'EUR', 1)",
            (account_id,),
        )
        connection.exec_driver_sql(
            "INSERT INTO categories (id, name_key, kind, nature, is_active) "
            "VALUES (?, 'category.income', 'income', 'variable', 1)",
            (category_id,),
        )
        connection.exec_driver_sql(
            "INSERT INTO rules (id, priority, mode, pattern, transaction_type, "
            "category_id, is_active, origin, hit_count) "
            "VALUES (?, 1, 'contains', 'salary', 'income', ?, 1, 'seed', 0)",
            (rule_id, category_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO transactions (id, account_id, operation_date, "
            "value_date, original_description, normalized_description, "
            "amount_minor, balance_minor, currency_code, type, category_id, "
            "rule_id, origin, status, is_manual, identity_key) VALUES (?, ?, "
            "'2024-01-15', '2024-01-15', 'x', 'x', 10000, 0, 'EUR', 'income', "
            "?, ?, 'rule', 'pending', 0, 'existing')",
            (txn_id, account_id, category_id, rule_id),
        )
    engine.dispose()

    _run_alembic(db_path, "upgrade", "head")

    current = _run_alembic(db_path, "current")
    assert "b7d4e1f20a93 (head)" in current.stdout
    _run_alembic(db_path, "check")
    with make_engine(f"sqlite:///{db_path}").connect() as connection:
        rule_category = connection.exec_driver_sql(
            "SELECT category_id FROM rules WHERE id = ?", (rule_id,)
        ).scalar()
        txn_rule = connection.exec_driver_sql(
            "SELECT rule_id FROM transactions WHERE id = ?", (txn_id,)
        ).scalar()
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert rule_category == category_id
    assert txn_rule == rule_id


def test_downgrade_with_rule_validated_rows_is_refused(tmp_path: Path) -> None:
    """The validated-by-rule marker cannot be dropped while rows carry it."""
    db_path = tmp_path / "marked.db"
    _run_alembic(db_path, "upgrade", "head")
    session = sessionmaker(bind=make_engine(f"sqlite:///{db_path}"))()
    account = _make_account(session)
    txn = _make_transaction(session, account=account, identity_key="marked")
    txn.validated_by_rule = True
    session.commit()
    session.close()

    result = _run_alembic(db_path, "downgrade", "-1", check=False)

    assert result.returncode != 0
    assert "validated-by-rule marker" in result.stderr
    assert "b7d4e1f20a93 (head)" in _run_alembic(db_path, "current").stdout
    _run_alembic(db_path, "check")


def test_downgrade_without_rule_validated_rows_drops_marker(tmp_path: Path) -> None:
    """With no marked rows the marker column is dropped and re-added cleanly."""
    db_path = tmp_path / "unmarked.db"
    _run_alembic(db_path, "upgrade", "head")

    _run_alembic(db_path, "downgrade", "-1")
    _run_alembic(db_path, "upgrade", "head")

    assert "b7d4e1f20a93 (head)" in _run_alembic(db_path, "current").stdout
    _run_alembic(db_path, "check")
