"""Widget helpers shared by the Validate page's queue and guided views."""

from __future__ import annotations

from fiskal.db.models import Category, CostCenter, Person
from fiskal.domain.enums import TransactionType
from fiskal.i18n import translate
from fiskal.ui.labels import category_label, cost_center_label, person_label

TYPE_OPTIONS = [
    TransactionType.EXPENSE.value,
    TransactionType.INCOME.value,
    TransactionType.SAVINGS_INVESTMENT.value,
    TransactionType.REFUND.value,
]


def none_first_index(ids: list[object], value: object | None) -> int:
    """Return the selectbox index of ``value`` when ``None`` is option zero."""
    if value is None:
        return 0
    try:
        return ids.index(value) + 1
    except ValueError:
        return 0


def category_option_label(category: Category | None) -> str:
    """Return the label of a category option, or the prompt for ``None``."""
    if category is None:
        return translate("validate.choose_category")
    return category_label(category)


def cost_center_option_label(cost_center: CostCenter | None) -> str:
    """Return the label of a cost center option, or "None" for ``None``."""
    if cost_center is None:
        return translate("common.none")
    return cost_center_label(cost_center)


def person_option_label(person: Person | None) -> str:
    """Return the label of a person option, or "Unattributed" for ``None``."""
    if person is None:
        return translate("person.unattributed")
    return person_label(person.name)
