"""Settings page: cost centers, categories and classification rules."""

from __future__ import annotations

import streamlit as st

from fiskal.i18n import translate
from fiskal.ui.language import apply_profile_language
from fiskal.ui.messages import show_flashes
from fiskal.ui.session import open_session
from fiskal.ui.settings_categories import render_categories
from fiskal.ui.settings_cost_centers import render_cost_centers
from fiskal.ui.settings_rules import render_rules


SECTIONS = {
    "cost_centers": ("settings.tab_cost_centers", render_cost_centers),
    "categories": ("settings.tab_categories", render_categories),
    "rules": ("settings.tab_rules", render_rules),
}


def main() -> None:
    """Render the settings page.

    The section selector is a keyed widget, so the section the owner works in
    survives the rerun that follows a save, and messages are shown inside it.
    """
    apply_profile_language()
    st.set_page_config(page_title=translate("nav.settings"), page_icon="⚙️")
    st.header(translate("nav.settings"))
    section = st.radio(
        translate("nav.settings"),
        list(SECTIONS),
        format_func=lambda name: translate(SECTIONS[name][0]),
        horizontal=True,
        label_visibility="collapsed",
        key="settings_section",
    )
    show_flashes()
    with open_session() as session:
        SECTIONS[section][1](session)

if __name__ == "__main__":
    main()
