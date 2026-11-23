"""Tests for the v1 core data model.

All data is synthetic; no personal or bank-specific information is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import (
    Account,
    Category,
    CostCenter,
    Import,
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


def _make_transaction(  # noqa: PLR0913
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


def _run_alembic(db_path: Path, *args: str) -> None:
    alembic_ini = Path(__file__).resolve().parents[1] / "migrations" / "alembic.ini"
    env = {
        **dict(os.environ),
        "FISKAL_MIGRATION_URL": f"sqlite:///{db_path}",
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(alembic_ini),
            *args,
        ],
        env=env,
        check=True,
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
