"""Streamlit launcher.

This module is the entry point for ``streamlit run`` and the ``fiskal``
console script. It only wires pages together; all business logic lives
elsewhere. The navigation is built explicitly so every entry, including the
first one, gets a translated and consistently capitalized title.
"""

from __future__ import annotations

import streamlit as st

from fiskal.i18n import translate
from fiskal.ui.home import WORKING_PAGES, render_home
from fiskal.ui.language import apply_profile_language, render_language_selector


def main() -> None:
    """Configure the Streamlit application and run the page navigation."""
    apply_profile_language()
    st.set_page_config(
        page_title=translate("app.title"),
        page_icon="🏦",
        layout="centered",
    )
    pages = [
        st.Page(
            render_home,
            title=translate("nav.home"),
            icon="🏠",
            url_path="home",
            default=True,
        ),
        *(
            st.Page(path, title=translate(label_key), icon=icon)
            for path, label_key, icon in WORKING_PAGES
        ),
    ]
    render_language_selector()
    st.navigation(pages).run()


if __name__ == "__main__":
    main()
