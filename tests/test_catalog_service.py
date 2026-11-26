"""Tests for cost center and category management."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from fiskal.classification import (
    CatalogError,
    CategorySpec,
    create_category,
    create_cost_center,
    has_owner_cost_center,
    rename_cost_center,
    seed_neutral_data,
    set_category_active,
    set_cost_center_active,
    update_category,
)
from fiskal.db.models import Category, CostCenter
from fiskal.domain.enums import CategoryKind
from fiskal.domain.seed import DEFAULT_CATEGORIES, DEFAULT_COST_CENTERS
from fiskal.i18n import translate
from fiskal.ui.labels import category_label, cost_center_label, help_text
from tests.support.factories import (
    _account,
    _category,
    _cost_center,
    _rule,
    _transaction,
)

YEAR_MONTHS = 12
HALF_YEAR_MONTHS = 6


def _spec(**overrides: object) -> CategorySpec:
    values: dict[str, object] = {
        "name": "Property A taxes",
        "kind": "expense",
        "nature": "variable",
        "income_source": None,
        "default_proration_months": None,
    }
    values.update(overrides)
    return CategorySpec(**values)  # type: ignore[arg-type]


def test_create_cost_center_stores_plain_name(session: Session) -> None:
    """A created cost center has a custom name, no key, and is active."""
    created = create_cost_center(session, "  Flat   A ")

    assert created.custom_name == "Flat A"
    assert created.name_key is None
    assert created.is_active
    assert cost_center_label(created) == "Flat A"


@pytest.mark.parametrize("name", ["", "   "])
def test_create_cost_center_rejects_empty_name(session: Session, name: str) -> None:
    """An empty name is refused and nothing is written."""
    with pytest.raises(CatalogError):
        create_cost_center(session, name)
    assert session.query(CostCenter).count() == 0


def test_cost_center_names_are_unique_case_insensitively(session: Session) -> None:
    """Duplicate names, including seed translations, are refused."""
    seed_neutral_data(session)
    create_cost_center(session, "Flat A")

    with pytest.raises(CatalogError):
        create_cost_center(session, "flat a")
    with pytest.raises(CatalogError):
        create_cost_center(session, "Household")
    with pytest.raises(CatalogError):
        create_cost_center(session, translate("costcenter.household", "es"))


def test_rename_cost_center_keeps_seed_key(session: Session) -> None:
    """Renaming a seed item stores a custom name and keeps the key."""
    seed_neutral_data(session)
    household = (
        session.query(CostCenter).filter_by(name_key="costcenter.household").one()
    )

    rename_cost_center(session, household.id, "Home")

    assert household.name_key == "costcenter.household"
    assert cost_center_label(household) == "Home"
    seed_neutral_data(session)
    assert (
        session.query(CostCenter).filter_by(name_key="costcenter.household").count()
        == 1
    )
    assert cost_center_label(household) == "Home"


def test_rename_to_own_name_is_allowed_and_restores_key(session: Session) -> None:
    """Typing the seed name again clears the custom name."""
    seed_neutral_data(session)
    work = session.query(CostCenter).filter_by(name_key="costcenter.work").one()
    rename_cost_center(session, work.id, "Office")

    rename_cost_center(session, work.id, "Work")

    assert work.custom_name is None


def test_rename_unknown_cost_center_fails(session: Session) -> None:
    """Renaming a missing item raises a catalog error."""
    with pytest.raises(CatalogError):
        rename_cost_center(session, uuid4(), "X")


def test_cost_center_deactivate_and_reactivate(session: Session) -> None:
    """A cost center is deactivated and reactivated, never deleted."""
    created = create_cost_center(session, "Flat A")

    set_cost_center_active(session, created.id, active=False)
    assert not created.is_active
    set_cost_center_active(session, created.id, active=True)

    assert created.is_active
    assert session.query(CostCenter).count() == 1


def test_cost_center_with_active_rule_cannot_be_deactivated(session: Session) -> None:
    """A cost center still assigned by an active rule stays active."""
    category = _category(session)
    center = _cost_center(session)
    rule = _rule(session, category=category, cost_center=center)

    with pytest.raises(CatalogError):
        set_cost_center_active(session, center.id, active=False)

    rule.is_active = False
    session.commit()
    set_cost_center_active(session, center.id, active=False)
    assert not center.is_active


def test_cost_center_default_of_account_cannot_be_deactivated(session: Session) -> None:
    """An account's default cost center cannot be hidden."""
    center = _cost_center(session)
    _account(session, default_cc=center)

    with pytest.raises(CatalogError):
        set_cost_center_active(session, center.id, active=False)


def test_deactivated_cost_center_keeps_label_on_transactions(session: Session) -> None:
    """A deactivated cost center is still shown on its movements."""
    center = create_cost_center(session, "Flat A")
    txn = _transaction(session, account=_account(session), cost_center=center)

    set_cost_center_active(session, center.id, active=False)

    assert txn.cost_center_id == center.id
    assert cost_center_label(center) == "Flat A"


def test_has_owner_cost_center_ignores_seed_and_inactive(session: Session) -> None:
    """Only active owner-created cost centers count."""
    seed_neutral_data(session)
    assert not has_owner_cost_center(session)

    created = create_cost_center(session, "Flat A")
    assert has_owner_cost_center(session)

    set_cost_center_active(session, created.id, active=False)
    assert not has_owner_cost_center(session)


def test_create_expense_category(session: Session) -> None:
    """An expense category stores kind, nature and proration months."""
    created = create_category(
        session, _spec(nature="fixed", default_proration_months=12)
    )

    assert created.kind == "expense"
    assert created.nature == "fixed"
    assert created.income_source is None
    assert created.default_proration_months == YEAR_MONTHS
    assert category_label(created) == "Property A taxes"


def test_create_income_category_needs_source(session: Session) -> None:
    """An income category requires a valid income source."""
    with pytest.raises(CatalogError):
        create_category(session, _spec(kind="income"))
    with pytest.raises(CatalogError):
        create_category(session, _spec(kind="income", income_source="bogus"))

    created = create_category(
        session, _spec(name="Flat A rent", kind="income", income_source="rental")
    )
    assert created.income_source == "rental"


def test_expense_category_drops_income_source(session: Session) -> None:
    """An expense category never keeps an income source."""
    created = create_category(session, _spec(income_source="work"))

    assert created.income_source is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "},
        {"kind": "bogus"},
        {"nature": "bogus"},
        {"default_proration_months": 0},
        {"default_proration_months": -3},
    ],
)
def test_create_category_rejects_invalid_input(
    session: Session, overrides: dict[str, object]
) -> None:
    """Invalid input is refused and nothing is written."""
    with pytest.raises(CatalogError):
        create_category(session, _spec(**overrides))
    assert session.query(Category).count() == 0


def test_category_names_are_unique(session: Session) -> None:
    """Duplicate category names are refused, including seed names."""
    seed_neutral_data(session)
    create_category(session, _spec())

    with pytest.raises(CatalogError):
        create_category(session, _spec(name="PROPERTY A TAXES"))
    with pytest.raises(CatalogError):
        create_category(session, _spec(name="Rental expenses"))


def test_update_category_edits_fields(session: Session) -> None:
    """Updating replaces name, nature, source and proration months."""
    created = create_category(session, _spec())

    update_category(
        session,
        created.id,
        _spec(name="Property A costs", nature="fixed", default_proration_months=6),
    )

    assert category_label(created) == "Property A costs"
    assert created.nature == "fixed"
    assert created.default_proration_months == HALF_YEAR_MONTHS


def test_update_seed_category_without_rename_keeps_key(session: Session) -> None:
    """Editing other fields of a seed category does not freeze its name."""
    seed_neutral_data(session)
    housing = session.query(Category).filter_by(name_key="category.housing").one()

    update_category(
        session,
        housing.id,
        _spec(name=translate("category.housing"), nature="variable"),
    )

    assert housing.custom_name is None
    assert housing.nature == "variable"


def test_update_category_kind_blocked_when_in_use(session: Session) -> None:
    """The kind of a category used by a transaction cannot change."""
    category = _category(session)
    _transaction(session, account=_account(session), category=category)

    with pytest.raises(CatalogError):
        update_category(
            session, category.id, _spec(kind="income", income_source="work")
        )

    update_category(session, category.id, _spec(name="Groceries", kind="expense"))


def test_update_category_kind_allowed_when_unused(session: Session) -> None:
    """An unused category can change kind."""
    category = _category(session)

    update_category(
        session,
        category.id,
        _spec(name="Side income", kind="income", income_source="other"),
    )

    assert category.kind == CategoryKind.INCOME.value


def test_update_unknown_category_fails(session: Session) -> None:
    """Updating a missing category raises a catalog error."""
    with pytest.raises(CatalogError):
        update_category(session, uuid4(), _spec())


def test_category_deactivate_keeps_rows_and_label(session: Session) -> None:
    """A used category is deactivated, not deleted, and keeps its label."""
    category = create_category(session, _spec())
    txn = _transaction(session, account=_account(session), category=category)

    set_category_active(session, category.id, active=False)

    assert not category.is_active
    assert txn.category_id == category.id
    assert category_label(category) == "Property A taxes"
    set_category_active(session, category.id, active=True)
    assert category.is_active


def test_category_with_active_rule_cannot_be_deactivated(session: Session) -> None:
    """A category assigned by an active rule stays active until the rule changes."""
    category = _category(session)
    rule = _rule(session, category=category)

    with pytest.raises(CatalogError):
        set_category_active(session, category.id, active=False)

    rule.is_active = False
    session.commit()
    set_category_active(session, category.id, active=False)
    assert not category.is_active


def test_seed_items_have_help_text() -> None:
    """Every seed category and cost center has help text in both languages."""
    for item in (*DEFAULT_CATEGORIES, *DEFAULT_COST_CENTERS):
        for locale in ("en", "es"):
            assert not translate(f"{item.key}.help", locale).startswith("[")
    assert help_text(None) == ""
    assert help_text("category.housing") == translate("category.housing.help")
