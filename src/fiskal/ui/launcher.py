"""Streamlit launcher.

This module is the entry point for ``streamlit run`` and the ``fiskal``
console script. It only wires pages together; all business logic lives
elsewhere.
"""

from __future__ import annotations

import streamlit as st

from fiskal.i18n import translate


def main() -> None:
    """Configure the Streamlit application and render the welcome page."""
    st.set_page_config(
        page_title=translate("app.title"),
        page_icon="🏦",
        layout="centered",
    )

    st.title(translate("app.title"))
    st.subheader(translate("app.tagline"))
    st.write(translate("ui.no_data"))


if __name__ == "__main__":
    main()
