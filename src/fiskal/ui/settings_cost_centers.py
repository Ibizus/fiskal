"""Settings section: list, create, rename, deactivate and reactivate cost centers."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from fiskal.classification import (
    CatalogError,
    create_cost_center,
    rename_cost_center,
    set_cost_center_active,
)
from fiskal.db.models import CostCenter
from fiskal.i18n import translate
from fiskal.services import list_cost_centers
from fiskal.ui.labels import cost_center_label, help_text, status_marker
from fiskal.ui.messages import flash


def _render_create(session: Session) -> None:
    with st.form("settings_cost_center_create", clear_on_submit=True):
        name = st.text_input(translate("settings.name"))
        submitted = st.form_submit_button(
            translate("settings.cost_center_create"),
            key="settings_cost_center_create_submit",
        )
    if not submitted:
        return
    try:
        created = create_cost_center(session, name)
    except CatalogError as exc:
        st.error(str(exc))
        return
    flash(
        "success",
        translate("settings.cost_center_created").format(
            name=cost_center_label(created)
        ),
    )
    st.rerun()


def _render_item(session: Session, cost_center: CostCenter) -> None:
    key = f"settings_cc_{cost_center.id}"
    title = f"{cost_center_label(cost_center)} {status_marker(cost_center.is_active)}"
    with st.expander(title):
        description = help_text(cost_center.name_key)
        if description:
            st.caption(description)
        with st.form(f"{key}_rename"):
            name = st.text_input(
                translate("settings.name"),
                value=cost_center_label(cost_center),
                key=f"{key}_name",
            )
            renamed = st.form_submit_button(translate("settings.save"))
        toggled = st.button(
            translate(
                "settings.deactivate"
                if cost_center.is_active
                else "settings.reactivate"
            ),
            key=f"{key}_toggle",
        )
    try:
        if renamed:
            rename_cost_center(session, cost_center.id, name)
            flash("success", translate("settings.saved"))
            st.rerun()
        if toggled:
            set_cost_center_active(
                session, cost_center.id, active=not cost_center.is_active
            )
            flash("success", translate("settings.saved"))
            st.rerun()
    except CatalogError as exc:
        st.error(str(exc))


def render_cost_centers(session: Session) -> None:
    """Render the cost center management section."""
    st.caption(translate("settings.cost_centers_intro"))
    _render_create(session)
    for cost_center in list_cost_centers(session, active_only=False):
        _render_item(session, cost_center)
