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


def main() -> None:
    """Render the settings page."""
    apply_profile_language()
    st.set_page_config(page_title=translate("nav.settings"), page_icon="⚙️")
    st.header(translate("nav.settings"))
    show_flashes()
    cost_centers_tab, categories_tab, rules_tab = st.tabs(
        [
            translate("settings.tab_cost_centers"),
            translate("settings.tab_categories"),
            translate("settings.tab_rules"),
        ]
    )
    with open_session() as session:
        with cost_centers_tab:
            render_cost_centers(session)
        with categories_tab:
            render_categories(session)
        with rules_tab:
            render_rules(session)


if __name__ == "__main__":
    main()
