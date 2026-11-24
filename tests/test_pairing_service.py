"""Tests for the persistence-aware internal-transfer pairing service."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fiskal.classification.pairing_service import (
    confirm_pair,
    find_proposals_for_transaction_ids,
    reject_pair,
    unpair,
)
from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import Account, Transaction
from fiskal.domain.enums import (
    ClassificationOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.domain.transfer_pairing import PairingError
from fiskal.reports.helpers import (
    income_expense_total_minor,
    income_expense_transactions,
)


@pytest.fixture
def session() -> Session:
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def account_a(session: Session) -> Account:
    """Return a persisted EUR account named Account A."""
    account = Account(name="Account A", currency_code="EUR")
    session.add(account)
    session.commit()
    return account


@pytest.fixture
def account_b(session: Session) -> Account:
    """Return a persisted EUR account named Account B."""
    account = Account(name="Account B", currency_code="EUR")
    session.add(account)
    session.commit()
    return account


def _transaction(  # noqa: PLR0913
    session: Session,
    account: Account,
    *,
    amount_minor: int,
    operation_date: date | None = None,
    value_date: date | None = None,
    currency_code: str | None = None,
    status: TransactionStatus = TransactionStatus.PENDING,
    paired_transaction_id: object | None = None,
    identity_key: str | None = None,
) -> Transaction:
    day = operation_date or date(2024, 3, 15)
    txn = Transaction(
        account_id=account.id,
        operation_date=day,
        value_date=value_date or day,
        original_description="SYNTHETIC",
        normalized_description="synthetic",
        counterparty="Synthetic",
        amount_minor=amount_minor,
        balance_minor=0,
        currency_code=currency_code or account.currency_code,
        transaction_type=(
            TransactionType.INCOME if amount_minor >= 0 else TransactionType.EXPENSE
        ).value,
        origin=ClassificationOrigin.MANUAL.value,
        status=status.value,
        identity_key=identity_key or str(uuid4()),
        paired_transaction_id=paired_transaction_id,
    )
    session.add(txn)
    session.commit()
    return txn


def test_confirm_pair_links_and_marks_internal_transfer(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Confirming a pair links both sides and sets the internal-transfer type."""
    outgoing = _transaction(session, account_a, amount_minor=-10_000)
    incoming = _transaction(session, account_b, amount_minor=10_000)

    confirm_pair(session, outgoing.id, incoming.id)

    assert outgoing.paired_transaction_id == incoming.id
    assert incoming.paired_transaction_id == outgoing.id
    for txn in (outgoing, incoming):
        assert txn.transaction_type == TransactionType.INTERNAL_TRANSFER.value
        assert txn.category_id is None
        assert txn.cost_center_id is None
        assert txn.origin == ClassificationOrigin.MANUAL.value


def test_reject_pair_is_remembered(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """A rejected pair is not proposed again."""
    outgoing = _transaction(session, account_a, amount_minor=-10_000)
    incoming = _transaction(session, account_b, amount_minor=10_000)

    reject_pair(session, outgoing.id, incoming.id)

    proposals = find_proposals_for_transaction_ids(session, [outgoing.id, incoming.id])
    assert proposals == []


def test_unpair_restores_unclassified_pending(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Unpairing restores both sides to their default type and pending status."""
    outgoing = _transaction(session, account_a, amount_minor=-10_000)
    incoming = _transaction(session, account_b, amount_minor=10_000)
    confirm_pair(session, outgoing.id, incoming.id)

    unpair(session, outgoing.id, incoming.id)

    assert outgoing.paired_transaction_id is None
    assert incoming.paired_transaction_id is None
    assert outgoing.transaction_type == TransactionType.EXPENSE.value
    assert incoming.transaction_type == TransactionType.INCOME.value
    assert outgoing.status == TransactionStatus.PENDING.value
    assert incoming.status == TransactionStatus.PENDING.value


def test_cannot_confirm_same_account(session: Session, account_a: Account) -> None:
    """Two transactions in the same account cannot be paired."""
    first = _transaction(session, account_a, amount_minor=-10_000)
    second = _transaction(session, account_a, amount_minor=10_000)

    with pytest.raises(PairingError, match="same account"):
        confirm_pair(session, first.id, second.id)


def test_cannot_confirm_currency_mismatch(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Transactions with different currencies cannot be paired."""
    outgoing = _transaction(
        session, account_a, amount_minor=-10_000, currency_code="EUR"
    )
    incoming = _transaction(
        session, account_b, amount_minor=10_000, currency_code="USD"
    )

    with pytest.raises(PairingError, match="currency"):
        confirm_pair(session, outgoing.id, incoming.id)


def test_cannot_confirm_amount_mismatch(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Transactions whose amounts are not exact opposites cannot be paired."""
    outgoing = _transaction(session, account_a, amount_minor=-10_000)
    incoming = _transaction(session, account_b, amount_minor=9_999)

    with pytest.raises(PairingError, match="amounts"):
        confirm_pair(session, outgoing.id, incoming.id)


def test_cannot_confirm_already_paired(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """A transaction already in a pair cannot be paired again."""
    outgoing = _transaction(session, account_a, amount_minor=-10_000)
    incoming = _transaction(session, account_b, amount_minor=10_000)
    other = _transaction(session, account_b, amount_minor=10_000)
    confirm_pair(session, outgoing.id, incoming.id)

    with pytest.raises(PairingError, match="already paired"):
        confirm_pair(session, outgoing.id, other.id)


def test_cannot_confirm_validated_transaction(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Validated transactions cannot be reclassified by pairing."""
    outgoing = _transaction(
        session, account_a, amount_minor=-10_000, status=TransactionStatus.VALIDATED
    )
    incoming = _transaction(session, account_b, amount_minor=10_000)

    with pytest.raises(PairingError, match="validated"):
        confirm_pair(session, outgoing.id, incoming.id)


def test_validated_transactions_are_not_proposed(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Validated rows are neither proposed as candidates nor used as sources."""
    outgoing = _transaction(session, account_a, amount_minor=-10_000)
    validated = _transaction(
        session, account_b, amount_minor=10_000, status=TransactionStatus.VALIDATED
    )
    pending = _transaction(session, account_b, amount_minor=10_000)

    proposals = find_proposals_for_transaction_ids(session, [outgoing.id])
    assert [(p.source_id, p.candidate_id) for p in proposals] == [
        (outgoing.id, pending.id)
    ]
    assert find_proposals_for_transaction_ids(session, [validated.id]) == []


def test_candidates_outside_window_are_not_proposed(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Only counterparts within the value-date window are proposed."""
    outgoing = _transaction(
        session, account_a, amount_minor=-10_000, operation_date=date(2024, 3, 15)
    )
    _transaction(
        session, account_b, amount_minor=10_000, operation_date=date(2024, 3, 19)
    )
    near = _transaction(
        session, account_b, amount_minor=10_000, operation_date=date(2024, 3, 18)
    )

    proposals = find_proposals_for_transaction_ids(session, [outgoing.id])
    assert [p.candidate_id for p in proposals] == [near.id]


def test_internal_transfers_are_excluded_from_income_expense_total(
    session: Session, account_a: Account, account_b: Account
) -> None:
    """Paired internal transfers do not count in merged income/expense totals."""
    _transaction(session, account_a, amount_minor=-5_000, identity_key="external-a")
    _transaction(session, account_b, amount_minor=7_000, identity_key="external-b")
    _transaction(session, account_a, amount_minor=-10_000, identity_key="out")
    _transaction(session, account_b, amount_minor=10_000, identity_key="in")

    outgoing = (
        session.query(Transaction)
        .filter_by(account_id=account_a.id, amount_minor=-10_000)
        .one()
    )
    incoming = (
        session.query(Transaction)
        .filter_by(account_id=account_b.id, amount_minor=10_000)
        .one()
    )
    confirm_pair(session, outgoing.id, incoming.id)

    counted = session.execute(income_expense_transactions()).scalars().all()
    counted_ids = {txn.id for txn in counted}
    external_total = -5_000 + 7_000
    assert outgoing.id not in counted_ids
    assert incoming.id not in counted_ids
    assert income_expense_total_minor(session) == external_total
