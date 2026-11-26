"""UI smoke tests for the Validate page's guided by-concept view."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from fiskal.classification import GroupKey, seed_neutral_data
from fiskal.db.models import Account, GuidedAction, Rule, Transaction
from fiskal.db.session import build_session_factory
from fiskal.domain.enums import (
    CategoryKind,
    GroupDirection,
    RuleOrigin,
    TransactionStatus,
    TransactionType,
)
from fiskal.services import list_categories
from fiskal.ui.validate_guided import GROUP_PAGE_SIZE, group_widget_key

PAGE = Path(__file__).resolve().parents[1] / "src/fiskal/ui/pages/2_Validate.py"
SHOP_ROWS = 4
BIG_ROWS = 300
EXTRA_GROUPS = 12
SECOND_PAGE_GROUPS = EXTRA_GROUPS - GROUP_PAGE_SIZE
SELECTBOXES_PER_GROUP = 4
VIEW_KEY = "validate_view"
EXPENSE = GroupDirection.EXPENSE


def _factory(profile: Path):  # type: ignore[no-untyped-def]
    return build_session_factory(f"sqlite:///{profile / 'profile.db'}")


def _seed(profile: Path, concepts: dict[tuple[str, int], int]) -> None:
    """Create an account and ``count`` pending rows per (description, amount)."""
    with _factory(profile)() as session:
        seed_neutral_data(session)
        account = Account(name="Demo", currency_code="EUR")
        session.add(account)
        session.commit()
        for (description, amount), count in concepts.items():
            session.add_all(
                Transaction(
                    account_id=account.id,
                    operation_date=date(2024, 1 + index % 12, 1 + index % 28),
                    value_date=date(2024, 1 + index % 12, 1 + index % 28),
                    original_description=description.upper(),
                    normalized_description=description,
                    counterparty=None,
                    amount_minor=amount,
                    balance_minor=0,
                    currency_code="EUR",
                    transaction_type=(
                        TransactionType.INCOME.value
                        if amount >= 0
                        else TransactionType.EXPENSE.value
                    ),
                    origin="manual",
                    status=TransactionStatus.PENDING.value,
                    identity_key=f"{description}|{amount}|{index}|{uuid4()}",
                )
                for index in range(count)
            )
        session.commit()


def _expense_index(profile: Path) -> int:
    with _factory(profile)() as session:
        categories = list_categories(session)
    return (
        next(
            i for i, c in enumerate(categories) if c.kind == CategoryKind.EXPENSE.value
        )
        + 1
    )


def _statuses(profile: Path, description: str) -> set[str]:
    with _factory(profile)() as session:
        return set(
            session.execute(
                select(Transaction.status).where(
                    Transaction.normalized_description == description
                )
            ).scalars()
        )


def _user_rules(profile: Path) -> list[Rule]:
    with _factory(profile)() as session:
        rules = list(
            session.execute(
                select(Rule).where(Rule.origin == RuleOrigin.USER.value)
            ).scalars()
        )
        session.expunge_all()
        return rules


def _open_guided() -> AppTest:
    app = AppTest.from_file(str(PAGE)).run()
    app.radio(key=VIEW_KEY).set_value("by_concept").run()
    assert not app.exception, f"App raised an exception: {app.exception}"
    return app


def _shop_key() -> str:
    return group_widget_key(GroupKey("shop", EXPENSE))


def _confirm_shop(app: AppTest, profile: Path, *, create_rule: bool) -> None:
    base = _shop_key()
    app.selectbox(key=f"{base}_category").select_index(_expense_index(profile))
    if create_rule:
        app.checkbox(key=f"{base}_create_rule").check()
    else:
        app.checkbox(key=f"{base}_create_rule").uncheck()
    app.button(key=f"{base}_submit").click().run()
    assert not app.exception, f"App raised an exception: {app.exception}"


def test_guided_view_lists_groups_largest_first_and_hides_singletons(
    isolated_profile: Path,
) -> None:
    """The by-concept view shows repeated descriptions, biggest group first."""
    _seed(
        isolated_profile,
        {("shop", -1000): SHOP_ROWS, ("gym", -3000): 2, ("one-off", -500): 1},
    )

    app = _open_guided()

    titles = [text.value for text in app.text]
    assert titles == ["shop · Expenses", "gym · Expenses"]
    assert any("2 groups hold 6 pending movements." in c.value for c in app.caption)


def test_guided_view_shows_pending_count_and_classified_percent(
    isolated_profile: Path,
) -> None:
    """The top of the page shows the pending count and the share classified."""
    _seed(isolated_profile, {("shop", -1000): SHOP_ROWS})

    app = AppTest.from_file(str(PAGE)).run()

    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {"Pending movements": "4", "Classified": "0%"}


def test_guided_confirm_with_rule_validates_the_group_and_creates_the_rule(
    isolated_profile: Path,
) -> None:
    """Confirming with the default rule validates every row and creates the rule."""
    _seed(isolated_profile, {("shop", -1000): SHOP_ROWS, ("gym", -3000): 2})

    app = _open_guided()
    assert app.text_input(key=f"{_shop_key()}_pattern").value == "shop"
    _confirm_shop(app, isolated_profile, create_rule=True)

    assert _statuses(isolated_profile, "shop") == {TransactionStatus.VALIDATED.value}
    assert _statuses(isolated_profile, "gym") == {TransactionStatus.PENDING.value}
    (rule,) = _user_rules(isolated_profile)
    assert rule.pattern == "shop"
    assert [s.value for s in app.success] == [
        "4 movements validated.",
        "Rule created. It also validated 0 other pending movements.",
    ]
    assert [text.value for text in app.text if "·" in text.value][:1] == [
        "gym · Expenses"
    ]


def test_guided_confirm_without_rule_creates_no_rule(isolated_profile: Path) -> None:
    """Unchecking the rule option validates the rows and leaves no rule behind."""
    _seed(isolated_profile, {("shop", -1000): SHOP_ROWS})

    app = _open_guided()
    _confirm_shop(app, isolated_profile, create_rule=False)

    assert _statuses(isolated_profile, "shop") == {TransactionStatus.VALIDATED.value}
    assert _user_rules(isolated_profile) == []
    assert [s.value for s in app.success] == ["4 movements validated."]


def test_guided_confirm_requires_a_category(isolated_profile: Path) -> None:
    """Confirming without a category shows an error and changes nothing."""
    _seed(isolated_profile, {("shop", -1000): SHOP_ROWS})

    app = _open_guided()
    app.button(key=f"{_shop_key()}_submit").click().run()

    assert [e.value for e in app.error] == ["Choose a category before confirming."]
    assert _statuses(isolated_profile, "shop") == {TransactionStatus.PENDING.value}


def test_guided_group_with_wide_spread_warns_and_defaults_to_no_rule(
    isolated_profile: Path,
) -> None:
    """Widely differing amounts warn and leave the rule option unchecked."""
    _seed(isolated_profile, {("shop", -500): 2, ("shop", -20000): 1})

    app = _open_guided()

    assert len(app.warning) == 1
    assert "differ widely" in app.warning[0].value
    assert app.checkbox(key=f"{_shop_key()}_create_rule").value is False


def test_guided_group_with_similar_amounts_has_no_warning_and_rule_on(
    isolated_profile: Path,
) -> None:
    """A steady group shows no warning and proposes a rule by default."""
    _seed(isolated_profile, {("shop", -1299): 2, ("shop", -1399): 1})

    app = _open_guided()

    assert not app.warning
    assert app.checkbox(key=f"{_shop_key()}_create_rule").value is True


def test_guided_undo_returns_group_and_deactivates_rule(
    isolated_profile: Path,
) -> None:
    """Undo returns the group to pending and turns off the rule it created."""
    _seed(isolated_profile, {("shop", -1000): SHOP_ROWS})
    app = _open_guided()
    _confirm_shop(app, isolated_profile, create_rule=True)
    with _factory(isolated_profile)() as session:
        action = session.execute(select(GuidedAction)).scalar_one()
        action_id: UUID = action.id

    app.button(key=f"guided_undo_{action_id}").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    assert _statuses(isolated_profile, "shop") == {TransactionStatus.PENDING.value}
    (rule,) = _user_rules(isolated_profile)
    assert rule.is_active is False
    assert "4 movements returned to pending." in [s.value for s in app.success]
    assert [text.value for text in app.text][0] == "shop · Expenses"


def test_guided_undo_some_rows(isolated_profile: Path) -> None:
    """Selected movements of a validated group can be returned one by one."""
    _seed(isolated_profile, {("shop", -1000): SHOP_ROWS})
    app = _open_guided()
    _confirm_shop(app, isolated_profile, create_rule=False)
    with _factory(isolated_profile)() as session:
        action_id = session.execute(select(GuidedAction.id)).scalar_one()

    multiselect = app.multiselect(key=f"guided_undo_rows_{action_id}")
    first = multiselect.options[0]
    multiselect.select(first).run()
    app.button(key=f"guided_undo_rows_button_{action_id}").click().run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    statuses = [row.status for row in _rows(isolated_profile, "shop")]
    assert statuses.count(TransactionStatus.PENDING.value) == 1
    assert statuses.count(TransactionStatus.VALIDATED.value) == SHOP_ROWS - 1


def _rows(profile: Path, description: str) -> list[Transaction]:
    with _factory(profile)() as session:
        rows = list(
            session.execute(
                select(Transaction).where(
                    Transaction.normalized_description == description
                )
            ).scalars()
        )
        session.expunge_all()
        return rows


def test_guided_groups_are_paginated(isolated_profile: Path) -> None:
    """More groups than one page are split, and the next page shows the rest."""
    _seed(
        isolated_profile,
        {(f"concept {index:02d}", -1000): 2 for index in range(EXTRA_GROUPS)},
    )

    app = _open_guided()
    first_page = [text.value for text in app.text]
    assert len(first_page) == GROUP_PAGE_SIZE
    assert any("Page 1 of 2" in c.value for c in app.caption)

    app.number_input(key="guided_page").set_value(2).run()

    assert not app.exception, f"App raised an exception: {app.exception}"
    second_page = [text.value for text in app.text]
    assert len(second_page) == SECOND_PAGE_GROUPS
    assert set(second_page).isdisjoint(first_page)


def test_guided_view_has_no_widget_per_row(isolated_profile: Path) -> None:
    """Hundreds of rows in one group still render a single group form."""
    _seed(isolated_profile, {("shop", -1000): BIG_ROWS})

    app = _open_guided()

    assert len(app.selectbox) == SELECTBOXES_PER_GROUP
    assert len(app.text) == 1


def test_guided_view_leaves_transfer_proposal_legs_out(
    isolated_profile: Path,
) -> None:
    """Movements in an open internal-transfer proposal are not in any group."""
    _seed(isolated_profile, {("shop", -1000): 2})
    with _factory(isolated_profile)() as session:
        other = Account(name="Other", currency_code="EUR")
        session.add(other)
        session.commit()
        account = session.execute(
            select(Account).where(Account.name == "Demo")
        ).scalar_one()
        for owner, amount in ((account, -7777), (other, 7777)):
            session.add(
                Transaction(
                    account_id=owner.id,
                    operation_date=date(2024, 2, 1),
                    value_date=date(2024, 2, 1),
                    original_description="shop",
                    normalized_description="shop",
                    amount_minor=amount,
                    balance_minor=0,
                    currency_code="EUR",
                    transaction_type=TransactionType.EXPENSE.value,
                    origin="manual",
                    status=TransactionStatus.PENDING.value,
                    identity_key=f"leg-{amount}",
                )
            )
        session.commit()

    app = _open_guided()

    assert any("1 groups hold 2 pending movements." in c.value for c in app.caption)
    confirm_buttons = [b for b in app.button if str(b.key).startswith("pair_confirm_")]
    assert len(confirm_buttons) == 1
