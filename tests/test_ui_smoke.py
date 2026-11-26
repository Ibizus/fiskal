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

from fiskal.classification import (
    RuleSpec,
    create_cost_center,
    create_rule,
    seed_neutral_data,
    set_cost_center_active,
    set_rule_active,
)
from fiskal.db.models import (
    Account,
    AccountHolder,
    Category,
    CostCenter,
    Import,
    Rule,
    Transaction,
)
from fiskal.db.session import build_session_factory
from fiskal.domain.enums import CategoryKind, TransactionStatus, TransactionType
from fiskal.i18n import set_active_locale
from fiskal.profile_settings import get_language, set_language
from fiskal.services import list_categories
from fiskal.ui.session import _session_factory

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "fiskal" / "ui"
LAUNCHER = UI_DIR / "launcher.py"
PAGES_DIR = UI_DIR / "pages"
QUEUE_PAGE_SIZE = 25
PRORATION_MONTHS = 12


@pytest.fixture
def isolated_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cached session factory at a fresh temporary database."""
    monkeypatch.setenv("FISKAL_DB_URL", f"sqlite:///{tmp_path / 'profile.db'}")
    _session_factory.clear()
    yield tmp_path
    _session_factory.clear()


def test_home_page_renders_empty_profile(isolated_profile: Path) -> None:
    """Home shows no pending movements and no import for a fresh profile."""
    app = AppTest.from_file(str(LAUNCHER)).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Home" in [h.value for h in app.header]
    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {
        "Pending movements": "0",
        "Last import": "No statement imported yet",
    }


def test_home_page_shows_pending_count_and_last_import(
    isolated_profile: Path,
) -> None:
    """Home shows the pending queue size and the date of the newest import."""
    _seed_pending(isolated_profile, [-1000, -2000])
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        account = session.execute(select(Account)).scalar_one()
        session.add(Import(account_id=account.id, file_name="a.xls", file_hash="h"))
        session.commit()

    app = AppTest.from_file(str(LAUNCHER)).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Pending movements"] == "2"
    assert metrics["Last import"] == f"{date.today():%Y-%m-%d}"


def test_home_page_links_to_working_pages(isolated_profile: Path) -> None:
    """Home links to every working page."""
    app = AppTest.from_file(str(LAUNCHER)).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    labels = [link.label for link in app.get("page_link")]
    assert labels == ["Import", "Validate", "Transactions", "Settings"]


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
    assert 'Holder "Ada" added.' in [s.value for s in app.success]

    app.text_input(key="new_account_name").input("Checking")
    app.text_input(key="new_account_hint").input("0042")
    app.multiselect(key="new_account_holders").select(SimpleNamespace(name="Ada"))
    app.button(key="new_account_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.selectbox(key="import_account").value.name == "Checking"
    assert 'Account "Checking" created.' in [s.value for s in app.success]
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
    app.button(key=f"validate_{txn_id}_submit").click().run()

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
    app.button(key=f"validate_{txn_id}_submit").click().run()

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
    app.button(key=f"validate_{txn_id}_submit").click().run()

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


def test_import_revert_shows_success_message(isolated_profile: Path) -> None:
    """Reverting an import shows the confirmation after the page reruns."""
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        seed_neutral_data(session)
        account = Account(name="Demo", currency_code="EUR")
        session.add(account)
        session.commit()
        session.add(Import(account_id=account.id, file_name="demo.xls", file_hash="h"))
        session.commit()

    app = AppTest.from_file(str(PAGES_DIR / "1_Import.py")).run()
    revert = next(b for b in app.button if b.label == "Revert import")
    revert.click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Import reverted." in [s.value for s in app.success]
    with factory() as session:
        assert session.execute(select(Import)).first() is None


def test_import_page_reports_empty_holder(isolated_profile: Path) -> None:
    """An empty holder name shows an error and no success message."""
    app = AppTest.from_file(str(PAGES_DIR / "1_Import.py")).run()
    app.button(key="new_holder_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert [e.value for e in app.error] == ["the holder name is required"]
    assert not app.success


def test_validate_has_one_submit_button_with_help(isolated_profile: Path) -> None:
    """Each queue row has a single, explained Validate button."""
    (txn_id,) = _seed_pending(isolated_profile, [-1000])

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    buttons = [b for b in app.button if b.key and b.key.startswith("validate_")]
    assert [(b.key, b.label) for b in buttons] == [
        (f"validate_{txn_id}_submit", "Validate")
    ]
    assert "validated with the type" in buttons[0].help


def test_validate_success_message_survives_the_rerun(isolated_profile: Path) -> None:
    """Validating a movement shows a confirmation on the page that follows."""
    (txn_id,) = _seed_pending(isolated_profile, [-1000])
    expense_index = _category_index(isolated_profile, CategoryKind.EXPENSE)

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()
    app.selectbox(key=f"validate_{txn_id}_category").select_index(expense_index)
    app.button(key=f"validate_{txn_id}_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert [s.value for s in app.success] == ["Movement validated."]
    assert _stored(isolated_profile, txn_id).status == TransactionStatus.VALIDATED.value
    app.run()
    assert not app.success, "the message is shown once"


def test_validate_rule_validates_every_matching_pending_row(
    isolated_profile: Path,
) -> None:
    """Creating a rule clears all matching rows and says how many it handled."""
    ids = _seed_pending(isolated_profile, [-1000, -1100, -1200])
    expense_index = _category_index(isolated_profile, CategoryKind.EXPENSE)

    app = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()
    app.selectbox(key=f"validate_{ids[0]}_category").select_index(expense_index)
    app.checkbox(key=f"validate_{ids[0]}_create_rule").check()
    app.button(key=f"validate_{ids[0]}_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert [s.value for s in app.success] == [
        "Movement validated.",
        "Rule created. It classified and validated 2 other pending movements.",
    ]
    for txn_id in ids:
        stored = _stored(isolated_profile, txn_id)
        assert stored.status == TransactionStatus.VALIDATED.value
    assert not [
        sb for sb in app.selectbox if sb.key and sb.key.endswith("_category")
    ], "the queue is empty"


def _seed_rule_validated(profile: Path) -> UUID:
    """Create one pending movement and validate it through a user rule."""
    (txn_id,) = _seed_pending(profile, [-1000])
    factory = build_session_factory(_profile_url(profile))
    with factory() as session:
        expense = next(
            c for c in list_categories(session) if c.kind == CategoryKind.EXPENSE.value
        )
        create_rule(
            session,
            RuleSpec(
                priority=0,
                mode="contains",
                pattern="synthetic party",
                transaction_type="expense",
                category_id=expense.id,
                cost_center_id=None,
                person_id=None,
                amount_min_minor=None,
                amount_max_minor=None,
                day_of_month_min=None,
                day_of_month_max=None,
                is_active=True,
            ),
        )
    return txn_id


def test_transactions_filter_shows_rule_validated_and_sends_back(
    isolated_profile: Path,
) -> None:
    """The rule-validated filter lists the rows and can return one to the queue."""
    txn_id = _seed_rule_validated(isolated_profile)
    assert _stored(isolated_profile, txn_id).validated_by_rule is True

    app = AppTest.from_file(str(PAGES_DIR / "3_Transactions.py")).run()
    app.selectbox(key="transactions_status").select("validated_by_rule").run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert len(app.dataframe) == 1
    assert app.dataframe[0].value["Status"].tolist() == ["Validated by rule"]

    app.button(key="transactions_send_back_button").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Movement sent back to the Validate queue." in [s.value for s in app.success]
    stored = _stored(isolated_profile, txn_id)
    assert stored.status == TransactionStatus.PENDING.value
    assert stored.validated_by_rule is False


SETTINGS_PAGE = PAGES_DIR / "4_Settings.py"


@pytest.fixture(autouse=True)
def _reset_locale() -> None:
    """Keep the process-wide active language from leaking between tests."""
    yield
    set_active_locale("en")


def test_validate_page_notice_without_owner_cost_center(
    isolated_profile: Path,
) -> None:
    """Validate points to Settings until the owner creates a cost center."""
    app = AppTest.from_file(str(LAUNCHER)).run()
    app.switch_page("pages/2_Validate.py").run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    infos = [info.value for info in app.info]
    assert any("cost center" in text for text in infos)
    assert "Open Settings" in [link.label for link in app.get("page_link")]


def test_validate_page_notice_disappears_with_cost_center(
    isolated_profile: Path,
) -> None:
    """The notice is gone once an owner-created cost center exists."""
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        seed_neutral_data(session)
        create_cost_center(session, "Flat A")

    app = AppTest.from_file(str(LAUNCHER)).run()
    app.switch_page("pages/2_Validate.py").run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert not any("cost center" in info.value for info in app.info)
    assert app.get("page_link") == []


def test_settings_page_renders_seed_items_with_help(isolated_profile: Path) -> None:
    """Settings lists seed items and shows their descriptions."""
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Settings" in [h.value for h in app.header]
    section = app.radio(key="settings_section")
    assert section.value == "cost_centers"
    assert list(section.options) == ["Cost centers", "Categories", "Rules"]
    captions = [caption.value for caption in app.caption]
    assert "Spending shared by the whole household." in captions
    _open_section(app, "categories")
    captions = [caption.value for caption in app.caption]
    assert "Groceries, restaurants and takeaway." in captions
    labels = [expander.label for expander in app.expander]
    assert "Rental expenses " in labels or "Rental expenses" in labels


def test_settings_creates_and_deactivates_cost_center(isolated_profile: Path) -> None:
    """A cost center can be created, renamed, deactivated and reactivated."""
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    app.text_input[0].input("Flat A")
    app.button(key="settings_cost_center_create_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert 'Cost center "Flat A" created.' in [s.value for s in app.success]
    flat = next(e for e in app.expander if e.label.startswith("Flat A"))
    assert flat is not None

    key = next(b.key for b in app.button if b.key and b.key.endswith("_toggle"))
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        created = session.execute(
            select(CostCenter).where(CostCenter.custom_name == "Flat A")
        ).scalar_one()
    toggle = app.button(key=f"settings_cc_{created.id}_toggle")
    toggle.click().run()
    assert not app.exception, f"App raised an exception: {app.exception}"
    with factory() as session:
        assert not session.get(CostCenter, created.id).is_active
    assert any(e.label.startswith("Flat A (inactive)") for e in app.expander)

    app.button(key=f"settings_cc_{created.id}_toggle").click().run()
    with factory() as session:
        assert session.get(CostCenter, created.id).is_active
    assert key


def test_settings_rejects_duplicate_cost_center(isolated_profile: Path) -> None:
    """A duplicate name shows an error instead of crashing."""
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    app.text_input[0].input("Household")
    app.button(key="settings_cost_center_create_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert any("already in use" in e.value for e in app.error)


def test_settings_shows_catalog_errors_in_spanish(isolated_profile: Path) -> None:
    """A catalog error is shown in the chosen interface language."""
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        seed_neutral_data(session)
        set_language(session, "es")
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    app.text_input[0].input("Household")
    app.button(key="settings_cost_center_create_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert [e.value for e in app.error] == ['El nombre "Household" ya está en uso.']


def test_settings_rule_editor_keeps_inactive_cost_center(
    isolated_profile: Path,
) -> None:
    """Editing a rule never swaps its inactive cost center for another one."""
    _seed_pending(isolated_profile, [])
    rule = next(r for r in _rules(isolated_profile) if r.pattern == "salary")
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        flat = create_cost_center(session, "Flat A")
        flat_id = flat.id
        stored = session.get(Rule, rule.id)
        stored.cost_center_id = flat_id
        session.commit()
        set_rule_active(session, rule.id, active=False)
        set_cost_center_active(session, flat_id, active=False)

    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    _open_section(app, "rules")
    selector = app.selectbox(key=f"settings_rule_{rule.id}_cost_center")
    assert selector.value is not None
    assert selector.value.id == flat_id

    app.text_input(key=f"settings_rule_{rule.id}_pattern").input("wages")
    app.button(
        key=f"FormSubmitter:settings_rule_{rule.id}_edit-Save rule"
    ).click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    saved = next(r for r in _rules(isolated_profile) if r.id == rule.id)
    assert saved.cost_center_id == flat_id


def _open_section(app: AppTest, name: str) -> None:
    """Choose a Settings section the way the owner does."""
    app.radio(key="settings_section").set_value(name).run()
    assert not app.exception, f"App raised an exception: {app.exception}"


def test_settings_rule_error_stays_in_rules_section(
    isolated_profile: Path,
) -> None:
    """A failed rule save keeps the owner in Rules with the error on screen."""
    _seed_pending(isolated_profile, [])
    rule = next(r for r in _rules(isolated_profile) if r.pattern == "salary")
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        flat = create_cost_center(session, "Flat A")
        stored = session.get(Rule, rule.id)
        stored.cost_center_id = flat.id
        session.commit()
        set_rule_active(session, rule.id, active=False)
        set_cost_center_active(session, flat.id, active=False)

    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    _open_section(app, "rules")
    app.text_input(key=f"settings_rule_{rule.id}_pattern").input("wages")
    app.button(
        key=f"FormSubmitter:settings_rule_{rule.id}_edit-Save rule"
    ).click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.radio(key="settings_section").value == "rules"
    assert any("inactive" in e.value for e in app.error)


def test_settings_success_stays_in_categories_section(
    isolated_profile: Path,
) -> None:
    """A saved category shows its message without leaving Categories."""
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    _open_section(app, "categories")
    app.text_input(key="settings_category_create_name").input("Flat A taxes")
    app.button(key="settings_category_create_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert app.radio(key="settings_section").value == "categories"
    assert 'Category "Flat A taxes" created.' in [s.value for s in app.success]
    assert app.text_input(key="settings_category_create_name") is not None


def test_settings_creates_category(isolated_profile: Path) -> None:
    """An income category with a source can be created from the page."""
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    _open_section(app, "categories")
    app.text_input(key="settings_category_create_name").input("Flat A rent")
    app.selectbox(key="settings_category_create_kind").select("income")
    app.selectbox(key="settings_category_create_source").select("rental")
    app.text_input(key="settings_category_create_months").input("12")
    app.button(key="settings_category_create_submit").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert 'Category "Flat A rent" created.' in [s.value for s in app.success]
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        created = session.execute(
            select(Category).where(Category.custom_name == "Flat A rent")
        ).scalar_one()
        assert created.kind == "income"
        assert created.income_source == "rental"
        assert created.default_proration_months == PRORATION_MONTHS


def test_settings_edits_and_deactivates_rule(isolated_profile: Path) -> None:
    """A seed rule can be edited and deactivated from the Rules tab."""
    _seed_pending(isolated_profile, [])
    rule = next(r for r in _rules(isolated_profile) if r.pattern == "salary")
    app = AppTest.from_file(str(SETTINGS_PAGE)).run()
    _open_section(app, "rules")

    app.text_input(key=f"settings_rule_{rule.id}_pattern").input("wages")
    app.button(
        key=f"FormSubmitter:settings_rule_{rule.id}_edit-Save rule"
    ).click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert any("Rule saved" in s.value for s in app.success)
    assert (
        next(r for r in _rules(isolated_profile) if r.id == rule.id).pattern == "wages"
    )

    app.button(key=f"settings_rule_{rule.id}_toggle").click().run()
    assert not app.exception, f"App raised an exception: {app.exception}"
    assert not next(r for r in _rules(isolated_profile) if r.id == rule.id).is_active


def test_language_switch_is_saved_and_applied(isolated_profile: Path) -> None:
    """Choosing Spanish is stored in the profile and translates every page."""
    app = AppTest.from_file(str(LAUNCHER)).run()
    assert "Home" in [h.value for h in app.header]

    app.sidebar.selectbox(key="language_selector").select("es").run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert "Inicio" in [h.value for h in app.header]
    factory = build_session_factory(_profile_url(isolated_profile))
    with factory() as session:
        assert get_language(session) == "es"

    # A fresh run (a new session on the same profile) keeps the language, and
    # pages that were already built follow it.
    set_active_locale("en")
    validate = AppTest.from_file(str(PAGES_DIR / "2_Validate.py")).run()
    assert "Validar" in [h.value for h in validate.header]
    settings = AppTest.from_file(str(SETTINGS_PAGE)).run()
    assert "Ajustes" in [h.value for h in settings.header]


def test_default_language_when_unset(isolated_profile: Path) -> None:
    """Without a saved choice the interface stays in English."""
    app = AppTest.from_file(str(LAUNCHER)).run()

    assert app.sidebar.selectbox(key="language_selector").value == "en"
    assert "Home" in [h.value for h in app.header]
