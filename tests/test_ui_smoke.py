"""Smoke tests for the Streamlit user interface.

These tests use Streamlit's headless app testing support so they run without a
browser and exercise the real page code against a temporary profile database.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from fiskal.classification import seed_neutral_data
from fiskal.db.models import Account, AccountHolder, Rule, Transaction
from fiskal.db.session import build_session_factory
from fiskal.domain.enums import CategoryKind, TransactionStatus, TransactionType
from fiskal.services import list_categories
from fiskal.ui.session import _session_factory

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "fiskal" / "ui"
LAUNCHER = UI_DIR / "launcher.py"
PAGES_DIR = UI_DIR / "pages"
QUEUE_PAGE_SIZE = 25


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
    assert "Create an account above before importing a statement." in infos


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


def _profile_url(profile: Path) -> str:
    return f"sqlite:///{profile / 'profile.db'}"


def _add_pending(
    session,
    account: Account,
    *,
    amount_minor: int,
    identity: str,
    description: str = "synthetic movement",
    day: int = 15,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        operation_date=date(2024, 1, day),
        value_date=date(2024, 1, day),
        original_description=description,
        normalized_description=description,
        counterparty="Synthetic Party",
        amount_minor=amount_minor,
        balance_minor=0,
        currency_code="EUR",
        transaction_type=(
            TransactionType.INCOME.value
            if amount_minor >= 0
            else TransactionType.EXPENSE.value
        ),
        origin="manual",
        status=TransactionStatus.PENDING.value,
        identity_key=identity,
    )
    session.add(txn)
    session.commit()
    return txn


def _seed_pending(profile: Path, amounts: list[int]) -> list[UUID]:
    factory = build_session_factory(_profile_url(profile))
    with factory() as session:
        seed_neutral_data(session)
        account = Account(name="Demo", currency_code="EUR")
        session.add(account)
        session.commit()
        return [
            _add_pending(
                session, account, amount_minor=amount, identity=f"pending-{index}"
            ).id
            for index, amount in enumerate(amounts)
        ]


def _category_index(profile: Path, kind: CategoryKind) -> int:
    factory = build_session_factory(_profile_url(profile))
    with factory() as session:
        categories = list_categories(session)
    position = next(i for i, c in enumerate(categories) if c.kind == kind.value)
    return position + 1


def _stored(profile: Path, txn_id: UUID) -> Transaction:
    factory = build_session_factory(_profile_url(profile))
    with factory() as session:
        txn = session.get(Transaction, txn_id)
        assert txn is not None
        session.expunge(txn)
        return txn


def _rules(profile: Path) -> list[Rule]:
    factory = build_session_factory(_profile_url(profile))
    with factory() as session:
        rules = list(session.execute(select(Rule)).scalars())
        session.expunge_all()
        return rules


def test_import_page_creates_account_with_holder(isolated_profile: Path) -> None:
    """A holder and an account can be created from the import page."""
    app = AppTest.from_file(str(PAGES_DIR / "1_Import.py")).run()
    app.text_input(key="new_holder_name").input("Ada")
    app.button(key="new_holder_submit").click().run()
    assert not app.exception, f"App raised an exception: {app.exception}"

    app.text_input(key="new_account_name").input("Checking")
    app.text_input(key="new_account_hint").input("0042")
    app.multiselect(key="new_account_holders").select(SimpleNamespace(name="Ada"))
    app.button(key="new_account_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.selectbox(key="import_account").value.name == "Checking"
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        account = session.execute(select(Account)).scalar_one()
        assert account.account_hint == "0042"
        assert account.currency_code == "EUR"
        assert session.execute(select(AccountHolder)).scalar_one().account_id == (
            account.id
        )


def test_import_page_reports_invalid_account(isolated_profile: Path) -> None:
    """Invalid account input shows an error instead of crashing."""
    app = AppTest.from_file(str(PAGES_DIR / "1_Import.py")).run()
    app.text_input(key="new_account_name").input("Checking")
    app.text_input(key="new_account_hint").input("12a")
    app.button(key="new_account_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.error
    assert "Create an account above before importing a statement." in [
        info.value for info in app.info
    ]


def test_validate_confirm_requires_category(isolated_profile: Path) -> None:
    """Confirming an unclassified row without a category shows an error."""
    (txn_id,) = _seed_pending(isolated_profile, [-1000])

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()
    assert app.selectbox(key=f"validate_{txn_id}_category").value is None
    app.button(key=f"validate_{txn_id}_confirm").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert [error.value for error in app.error] == [
        "Choose a category before confirming."
    ]
    assert _stored(isolated_profile, txn_id).status == TransactionStatus.PENDING.value


def test_validate_rule_with_incompatible_category_shows_error(
    isolated_profile: Path,
) -> None:
    """A rule from an invalid correction reports an error instead of crashing."""
    (txn_id,) = _seed_pending(isolated_profile, [500])
    expense_index = _category_index(isolated_profile, CategoryKind.EXPENSE)

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()
    app.selectbox(key=f"validate_{txn_id}_category").select_index(expense_index)
    app.checkbox(key=f"validate_{txn_id}_create_rule").check()
    app.button(key=f"validate_{txn_id}_confirm").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.error
    assert _stored(isolated_profile, txn_id).status == TransactionStatus.PENDING.value


def test_validate_rule_uses_edited_pattern(isolated_profile: Path) -> None:
    """The rule pattern can be edited before the correction is submitted."""
    (txn_id,) = _seed_pending(isolated_profile, [-1000])
    seeded_rule_ids = {rule.id for rule in _rules(isolated_profile)}
    expense_index = _category_index(isolated_profile, CategoryKind.EXPENSE)

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()
    app.selectbox(key=f"validate_{txn_id}_category").select_index(expense_index)
    app.checkbox(key=f"validate_{txn_id}_create_rule").check()
    app.text_input(key=f"validate_{txn_id}_pattern").input("custom pattern")
    app.button(key=f"validate_{txn_id}_confirm").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    new_rules = [r for r in _rules(isolated_profile) if r.id not in seeded_rule_ids]
    assert [rule.pattern for rule in new_rules] == ["custom pattern"]
    stored = _stored(isolated_profile, txn_id)
    assert stored.status == TransactionStatus.VALIDATED.value


def test_validate_hides_rows_with_open_pair_proposal(isolated_profile: Path) -> None:
    """Both legs of a proposed transfer leave the queue until the pair is decided."""
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        seed_neutral_data(session)
        source_account = Account(name="Source", currency_code="EUR")
        target_account = Account(name="Target", currency_code="EUR")
        session.add_all([source_account, target_account])
        session.commit()
        outgoing = _add_pending(
            session, source_account, amount_minor=-5000, identity="out"
        ).id
        incoming = _add_pending(
            session, target_account, amount_minor=5000, identity="in"
        ).id
        other = _add_pending(
            session, source_account, amount_minor=-123, identity="other"
        ).id

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Internal transfer proposals" in [s.value for s in app.subheader]
    type_keys = {selectbox.key for selectbox in app.selectbox}
    assert f"validate_{other}_type" in type_keys
    assert f"validate_{outgoing}_type" not in type_keys
    assert f"validate_{incoming}_type" not in type_keys


def test_validate_queue_is_limited_to_one_page(isolated_profile: Path) -> None:
    """A long queue renders one page of rows and says so."""
    _seed_pending(isolated_profile, [-(100 + index) for index in range(30)])

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    type_keys = [sb.key for sb in app.selectbox if sb.key.endswith("_type")]
    assert len(type_keys) == QUEUE_PAGE_SIZE
    assert any(
        "Showing the first 25 of 30 pending movements." in caption.value
        for caption in app.caption
    )


def test_transactions_page_reports_limit(isolated_profile: Path) -> None:
    """The transactions page says when the listing hit its row limit."""
    _seed_pending(isolated_profile, [-1] * 500)

    app = AppTest.from_file(str(PAGES_DIR / "3_Transactions.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert any(
        "Showing only the 500 most recent matching movements." in caption.value
        for caption in app.caption
    )


def test_validate_preselects_history_type(isolated_profile: Path) -> None:
    """An applied history suggestion also preselects its transaction type."""
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        seed_neutral_data(session)
        account = Account(name="Demo", currency_code="EUR")
        session.add(account)
        session.commit()
        savings = next(
            category
            for category in list_categories(session)
            if category.name_key == "category.savings"
        )
        history = _add_pending(session, account, amount_minor=-2000, identity="old")
        history.transaction_type = TransactionType.SAVINGS_INVESTMENT.value
        history.category_id = savings.id
        history.status = TransactionStatus.VALIDATED.value
        session.commit()
        savings_id = savings.id
        pending_id = _add_pending(
            session, account, amount_minor=-3000, identity="new", day=20
        ).id

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.selectbox(key=f"validate_{pending_id}_type").value == (
        TransactionType.SAVINGS_INVESTMENT.value
    )
    assert app.selectbox(key=f"validate_{pending_id}_category").value.id == savings_id
