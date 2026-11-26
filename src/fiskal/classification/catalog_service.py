"""Create, edit and deactivate cost centers and categories.

Items the owner creates carry a plain-text ``custom_name``. Seed items carry a
translation ``name_key``; renaming one stores a ``custom_name`` next to the key,
so seeding (matched by key) never overwrites the owner's choice. Nothing is ever
deleted: transactions, rules and accounts may reference an item, so it is
deactivated instead, and deactivated items keep their label on the movements
that already use them. An item still used by an active rule cannot be
deactivated until the rule is changed, so a rule never silently keeps assigning
a hidden item.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from fiskal.db.models import Account, Category, CostCenter, Rule, Transaction
from fiskal.domain.enums import CategoryKind, CategoryNature, IncomeSource
from fiskal.i18n import SUPPORTED_LOCALES, translate

MAX_NAME_LENGTH = 255


class CatalogError(Exception):
    """Raised when a cost center or category operation is not allowed.

    Carries a translation key and its format parameters so the interface shows
    the reason in the owner's language.
    """

    def __init__(self, key: str, **params: object) -> None:
        """Store the translation key and the values its text refers to."""
        super().__init__(key)
        self.key = key
        self.params = params

    def message(self, locale: str | None = None) -> str:
        """Return the translated reason, in the active locale by default."""
        return translate(self.key, locale).format(**self.params)


@dataclass(frozen=True)
class CategorySpec:
    """The editable fields of a category, shared by create and update."""

    name: str
    kind: CategoryKind | str
    nature: CategoryNature | str
    income_source: IncomeSource | str | None
    default_proration_months: int | None


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise CatalogError("settings.error.name_empty")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise CatalogError("settings.error.name_too_long", max=MAX_NAME_LENGTH)
    return cleaned


def _names_of(name_key: str | None, custom_name: str | None) -> set[str]:
    """Every name an item answers to: its custom name and all translations."""
    names: set[str] = set()
    if custom_name:
        names.add(custom_name.casefold())
    if name_key:
        names.update(
            translate(name_key, locale).casefold() for locale in SUPPORTED_LOCALES
        )
    return names


def _apply_name(item: CostCenter | Category, name: str) -> None:
    """Store ``name`` on ``item``.

    Typing the item's own translated name (or leaving it unchanged while editing
    other fields) keeps or restores the translation key as the display name
    instead of freezing today's translation as a custom name.
    """
    if item.name_key and name.casefold() in _names_of(item.name_key, None):
        item.custom_name = None
    else:
        item.custom_name = name


def _ensure_unique(
    existing: list[tuple[UUID, str | None, str | None]],
    name: str,
    *,
    own_id: UUID | None,
) -> None:
    wanted = name.casefold()
    for item_id, name_key, custom_name in existing:
        if item_id != own_id and wanted in _names_of(name_key, custom_name):
            raise CatalogError("settings.error.name_in_use", name=name)


def _cost_center_rows(session: Session) -> list[tuple[UUID, str | None, str | None]]:
    return [
        (row.id, row.name_key, row.custom_name)
        for row in session.execute(select(CostCenter)).scalars()
    ]


def _category_rows(session: Session) -> list[tuple[UUID, str | None, str | None]]:
    return [
        (row.id, row.name_key, row.custom_name)
        for row in session.execute(select(Category)).scalars()
    ]


def _get_cost_center(session: Session, cost_center_id: UUID) -> CostCenter:
    cost_center = session.get(CostCenter, cost_center_id)
    if cost_center is None:
        raise CatalogError("settings.error.cost_center_missing")
    return cost_center


def _get_category(session: Session, category_id: UUID) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise CatalogError("settings.error.category_missing")
    return category


def _count(session: Session, statement: Select[Any]) -> int:
    return int(session.execute(statement).scalar_one())


def create_cost_center(session: Session, name: str) -> CostCenter:
    """Create an active cost center the owner named."""
    cleaned = _clean_name(name)
    _ensure_unique(_cost_center_rows(session), cleaned, own_id=None)
    cost_center = CostCenter(custom_name=cleaned, is_active=True)
    session.add(cost_center)
    session.commit()
    return cost_center


def rename_cost_center(session: Session, cost_center_id: UUID, name: str) -> CostCenter:
    """Rename a cost center; a seed item keeps its key and gains a custom name."""
    cost_center = _get_cost_center(session, cost_center_id)
    cleaned = _clean_name(name)
    _ensure_unique(_cost_center_rows(session), cleaned, own_id=cost_center_id)
    _apply_name(cost_center, cleaned)
    session.commit()
    return cost_center


def set_cost_center_active(
    session: Session, cost_center_id: UUID, *, active: bool
) -> CostCenter:
    """Deactivate or reactivate a cost center; it is never deleted."""
    cost_center = _get_cost_center(session, cost_center_id)
    if not active and cost_center.is_active:
        rules = _count(
            session,
            select(func.count())
            .select_from(Rule)
            .where(Rule.cost_center_id == cost_center_id, Rule.is_active.is_(True)),
        )
        accounts = _count(
            session,
            select(func.count())
            .select_from(Account)
            .where(
                Account.default_cost_center_id == cost_center_id,
                Account.is_active.is_(True),
            ),
        )
        if rules:
            raise CatalogError("settings.error.cost_center_in_rules", count=rules)
        if accounts:
            raise CatalogError("settings.error.cost_center_in_accounts", count=accounts)
    cost_center.is_active = active
    session.commit()
    return cost_center


def has_owner_cost_center(session: Session) -> bool:
    """Return whether the owner created at least one active cost center."""
    return (
        _count(
            session,
            select(func.count())
            .select_from(CostCenter)
            .where(
                CostCenter.name_key.is_(None),
                CostCenter.custom_name.is_not(None),
                CostCenter.is_active.is_(True),
            ),
        )
        > 0
    )


def _validated_category_fields(
    spec: CategorySpec,
) -> tuple[str, CategoryKind, CategoryNature, str | None, int | None]:
    name = _clean_name(spec.name)
    try:
        kind = CategoryKind(spec.kind)
        nature = CategoryNature(spec.nature)
    except ValueError as exc:
        raise CatalogError("settings.error.invalid_choice") from exc
    source: str | None = None
    if kind is CategoryKind.INCOME:
        try:
            source = IncomeSource(spec.income_source or "").value
        except ValueError as exc:
            raise CatalogError("settings.error.income_source_required") from exc
    months = spec.default_proration_months
    if months is not None and months <= 0:
        raise CatalogError("settings.error.proration_months_positive")
    return name, kind, nature, source, months


def create_category(session: Session, spec: CategorySpec) -> Category:
    """Create an active category from ``spec``; nothing is written when invalid."""
    name, kind, nature, source, months = _validated_category_fields(spec)
    _ensure_unique(_category_rows(session), name, own_id=None)
    category = Category(
        custom_name=name,
        kind=kind.value,
        nature=nature.value,
        income_source=source,
        default_proration_months=months,
        is_active=True,
    )
    session.add(category)
    session.commit()
    return category


def _category_is_used(session: Session, category_id: UUID) -> bool:
    for model in (Transaction, Rule):
        used = _count(
            session,
            select(func.count())
            .select_from(model)
            .where(model.category_id == category_id),
        )
        if used:
            return True
    return False


def update_category(
    session: Session, category_id: UUID, spec: CategorySpec
) -> Category:
    """Replace the editable fields of a category.

    The kind of a category already used by transactions or rules cannot change,
    because it would invalidate their classification.
    """
    category = _get_category(session, category_id)
    name, kind, nature, source, months = _validated_category_fields(spec)
    _ensure_unique(_category_rows(session), name, own_id=category_id)
    if kind.value != category.kind and _category_is_used(session, category_id):
        raise CatalogError("settings.error.kind_in_use")
    _apply_name(category, name)
    category.kind = kind.value
    category.nature = nature.value
    category.income_source = source
    category.default_proration_months = months
    session.commit()
    return category


def set_category_active(
    session: Session, category_id: UUID, *, active: bool
) -> Category:
    """Deactivate or reactivate a category; it is never deleted."""
    category = _get_category(session, category_id)
    if not active and category.is_active:
        rules = _count(
            session,
            select(func.count())
            .select_from(Rule)
            .where(Rule.category_id == category_id, Rule.is_active.is_(True)),
        )
        if rules:
            raise CatalogError("settings.error.category_in_rules", count=rules)
    category.is_active = active
    session.commit()
    return category
