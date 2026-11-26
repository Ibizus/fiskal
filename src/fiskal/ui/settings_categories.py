"""Settings section: list, create, edit and deactivate categories."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import (
    CatalogError,
    CategorySpec,
    create_category,
    set_category_active,
    update_category,
)
from fiskal.db.models import Category
from fiskal.domain.enums import CategoryKind, CategoryNature, IncomeSource
from fiskal.i18n import translate
from fiskal.services import list_categories
from fiskal.ui.forms import FormValueError, format_optional_int, parse_optional_int
from fiskal.ui.labels import category_label, help_text, status_marker
from fiskal.ui.messages import flash

_KINDS = [member.value for member in CategoryKind]
_NATURES = [member.value for member in CategoryNature]
_SOURCES = [member.value for member in IncomeSource]


def _spec_from_inputs(prefix: str, category: Category | None) -> CategorySpec | None:
    """Draw the category fields and return the spec, or ``None`` if unreadable."""
    name = st.text_input(
        translate("settings.name"),
        value=category_label(category) if category else "",
        key=f"{prefix}_name",
    )
    kind = st.selectbox(
        translate("settings.kind"),
        _KINDS,
        index=_KINDS.index(category.kind) if category else 0,
        format_func=lambda value: translate(f"settings.kind.{value}"),
        key=f"{prefix}_kind",
    )
    nature = st.selectbox(
        translate("settings.nature"),
        _NATURES,
        index=_NATURES.index(category.nature) if category else 1,
        format_func=lambda value: translate(f"settings.nature.{value}"),
        key=f"{prefix}_nature",
    )
    source = st.selectbox(
        translate("settings.income_source"),
        _SOURCES,
        index=_SOURCES.index(category.income_source)
        if category and category.income_source
        else 0,
        format_func=lambda value: translate(f"settings.source.{value}"),
        help=translate("settings.income_source_help"),
        key=f"{prefix}_source",
    )
    months_text = st.text_input(
        translate("settings.proration_months"),
        value=format_optional_int(category.default_proration_months)
        if category
        else "",
        help=translate("settings.proration_months_help"),
        key=f"{prefix}_months",
    )
    try:
        months = parse_optional_int(months_text)
    except FormValueError:
        st.error(translate("settings.invalid_number"))
        return None
    return CategorySpec(
        name=name,
        kind=kind,
        nature=nature,
        income_source=source if kind == CategoryKind.INCOME.value else None,
        default_proration_months=months,
    )


def _render_create(session: Session) -> None:
    with st.form("settings_category_create", clear_on_submit=True):
        spec = _spec_from_inputs("settings_category_create", None)
        submitted = st.form_submit_button(
            translate("settings.category_create"),
            key="settings_category_create_submit",
        )
    if not submitted or spec is None:
        return
    try:
        created = create_category(session, spec)
    except CatalogError as exc:
        st.error(str(exc))
        return
    flash(
        "success",
        translate("settings.category_created").format(name=category_label(created)),
    )
    st.rerun()


def _render_item(session: Session, category: Category) -> None:
    key = f"settings_cat_{category.id}"
    title = f"{category_label(category)} {status_marker(category.is_active)}"
    with st.expander(title):
        description = help_text(category.name_key)
        if description:
            st.caption(description)
        with st.form(f"{key}_edit"):
            spec = _spec_from_inputs(key, category)
            saved = st.form_submit_button(translate("settings.save"))
        toggled = st.button(
            translate(
                "settings.deactivate" if category.is_active else "settings.reactivate"
            ),
            key=f"{key}_toggle",
        )
    try:
        if saved and spec is not None:
            update_category(session, category.id, spec)
            flash("success", translate("settings.saved"))
            st.rerun()
        if toggled:
            set_category_active(session, category.id, active=not category.is_active)
            flash("success", translate("settings.saved"))
            st.rerun()
    except CatalogError as exc:
        st.error(str(exc))


def render_categories(session: Session) -> None:
    """Render the category management section."""
    st.caption(translate("settings.categories_intro"))
    _render_create(session)
    for category in list_categories(session, active_only=False):
        _render_item(session, category)
