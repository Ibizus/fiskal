"""Tests that pair proposals are available right after an import."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fiskal.classification.pairing_service import confirm_pair
from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.db.models import Account, Transaction
from fiskal.importers import OpenbankImporter, import_statement
from fiskal.importers.base import ImportSummary
from tests.support.openbank_synthetic import (
    GenerationOptions,
    Movement,
    build_statement_html,
)

_ACCOUNT_CODE_A = "12345678901234567890"
_ACCOUNT_CODE_B = "98765432109876543210"


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


def _options(code: str, movements: list[Movement]) -> GenerationOptions:
    return GenerationOptions(
        movement_count=len(movements),
        start_date=movements[-1].operation_date if movements else date(2024, 3, 1),
        account_code=code,
        opening_balance_minor=100_000,
        seed=42,
    )


def _write_statement(path: Path, code: str, movements: list[Movement]) -> Path:
    options = _options(code, movements)
    html = build_statement_html(options, movements)
    path.write_bytes(html.encode("iso-8859-1"))
    return path


def _movement(day: date, description: str, amount: int, balance: int) -> Movement:
    return Movement(
        operation_date=day,
        value_date=day,
        description=description.ljust(100),
        amount_minor=amount,
        balance_minor=balance,
    )


def test_import_summary_reports_proposed_pairs(
    session: Session, account_a: Account, account_b: Account, tmp_path: Path
) -> None:
    """After an import, the summary reports how many pair proposals exist."""
    day = date(2024, 3, 15)
    outgoing = [_movement(day, "Transfer to own account B", -10_000, 90_000)]
    incoming = [_movement(day, "Transfer from own account A", 10_000, 110_000)]

    first = _write_statement(tmp_path / "a.xls", _ACCOUNT_CODE_A, outgoing)
    second = _write_statement(tmp_path / "b.xls", _ACCOUNT_CODE_B, incoming)

    result_a = import_statement(
        session, OpenbankImporter(), account_a, "a.xls", first.read_bytes()
    )
    assert result_a.summary.proposed_pair_count == 0
    assert result_a.pair_proposals == ()

    result_b = import_statement(
        session, OpenbankImporter(), account_b, "b.xls", second.read_bytes()
    )
    assert result_b.summary.proposed_pair_count == 1
    assert len(result_b.pair_proposals) == 1
    proposal = result_b.pair_proposals[0]
    assert proposal.day_delta == 0


def test_import_proposal_can_be_confirmed(
    session: Session, account_a: Account, account_b: Account, tmp_path: Path
) -> None:
    """A proposal produced by an import can be confirmed as an internal transfer."""
    day = date(2024, 3, 15)
    outgoing = [_movement(day, "Transfer to own account B", -10_000, 90_000)]
    incoming = [_movement(day, "Transfer from own account A", 10_000, 110_000)]

    first = _write_statement(tmp_path / "a.xls", _ACCOUNT_CODE_A, outgoing)
    second = _write_statement(tmp_path / "b.xls", _ACCOUNT_CODE_B, incoming)

    import_statement(
        session, OpenbankImporter(), account_a, "a.xls", first.read_bytes()
    )
    result_b = import_statement(
        session, OpenbankImporter(), account_b, "b.xls", second.read_bytes()
    )

    proposal = result_b.pair_proposals[0]
    confirm_pair(session, proposal.source_id, proposal.candidate_id)

    txn_a = session.get(Transaction, proposal.source_id)
    txn_b = session.get(Transaction, proposal.candidate_id)
    if txn_a.account_id == account_b.id:
        txn_a, txn_b = txn_b, txn_a
    assert txn_a.paired_transaction_id == txn_b.id
    assert txn_b.paired_transaction_id == txn_a.id


def test_import_summary_default_has_zero_proposals() -> None:
    """The new summary field defaults to zero for callers that do not set it."""
    summary = ImportSummary(
        first_transaction_date=None,
        last_transaction_date=None,
        rows_read=0,
        new_count=0,
        duplicate_count=0,
        truncated_count=0,
        warnings=(),
        near_duplicates=(),
    )
    assert summary.proposed_pair_count == 0
