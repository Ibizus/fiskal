"""Smoke tests for the Streamlit user interface.

These tests use Streamlit's headless app testing support so they run without a
browser and exercise the real page code against a temporary profile database.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from fiskal.classification import seed_neutral_data
from fiskal.db.models import Account, Transaction
from fiskal.db.session import build_session_factory
from fiskal.domain.enums import TransactionStatus, TransactionType
from fiskal.ui.session import _session_factory

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "fiskal" / "ui"
LAUNCHER = UI_DIR / "launcher.py"
PAGES_DIR = UI_DIR / "pages"


@pytest.fixture
def isolated_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cached session factory at a fresh temporary database."""
    monkeypatch.setenv("FISKAL_DB_URL", f"sqlite:///{tmp_path / 'profile.db'}")
    _session_factory.clear()
    yield tmp_path
    _session_factory.clear()


def test_welcome_page_renders() -> None:
    """The welcome page runs without exceptions and shows the expected content."""
    app = AppTest.from_file(str(LAUNCHER)).run()

    assert not app.exception, f"App raised an exception: {app.exception}"

    titles = [t.value for t in app.title]
    assert "Fiskal" in titles, f"Expected 'Fiskal' title, got {titles}"

    subheaders = [s.value for s in app.subheader]
    tagline_msg = f"Expected tagline, got {subheaders}"
    assert "Local-first personal finance" in subheaders, tagline_msg


def test_import_page_renders(isolated_profile: Path) -> None:
    """The import page renders and reports that no account exists yet."""
    app = AppTest.from_file(str(PAGES_DIR / "1_Import.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Import" in [h.value for h in app.header]
    infos = [info.value for info in app.info]
    assert "Create an account before importing a statement." in infos


def test_validate_page_renders_empty_queue(isolated_profile: Path) -> None:
    """The validate page renders an empty queue message when nothing is pending."""
    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Validate" in [h.value for h in app.header]
    infos = [info.value for info in app.info]
    assert "Nothing to validate." in infos


def test_transactions_page_renders_empty(isolated_profile: Path) -> None:
    """The transactions page renders an empty message when there are no rows."""
    app = AppTest.from_file(str(PAGES_DIR / "3_Transactions.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Transactions" in [h.value for h in app.header]
    infos = [info.value for info in app.info]
    assert "No transactions match the filters." in infos


def _seed_demo_profile(db_path: Path) -> None:
    """Create a synthetic account and transaction in a temporary database."""
    factory = build_session_factory(f"sqlite:///{db_path}")

    with factory() as session:
        seed_neutral_data(session)
        account = Account(name="Demo", currency_code="EUR")
        session.add(account)
        session.commit()
        session.add(
            Transaction(
                account_id=account.id,
                operation_date=date(2024, 1, 15),
                value_date=date(2024, 1, 15),
                original_description="Synthetic demo movement",
                normalized_description="synthetic demo movement",
                counterparty="Demo Shop",
                amount_minor=-1234,
                balance_minor=0,
                currency_code="EUR",
                transaction_type=TransactionType.EXPENSE.value,
                origin="manual",
                status=TransactionStatus.PENDING.value,
                identity_key="demo-identity",
            )
        )
        session.commit()


def test_transactions_page_lists_rows(isolated_profile: Path) -> None:
    """The transactions page renders rows when the profile has transactions."""
    _seed_demo_profile(isolated_profile / "profile.db")

    app = AppTest.from_file(str(PAGES_DIR / "3_Transactions.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert len(app.info) == 0, "Expected a populated table, not an empty message"
