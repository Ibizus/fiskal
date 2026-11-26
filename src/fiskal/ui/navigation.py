"""Navigation helpers shared by the pages."""

from __future__ import annotations

import streamlit as st
from streamlit.errors import StreamlitAPIException

from fiskal.i18n import translate

SETTINGS_PAGE = "pages/4_Settings.py"


def show_settings_link() -> None:
    """Link to the Settings page.

    A page opened on its own (outside the launcher's navigation) cannot link to
    its siblings; the link is simply left out then.
    """
    try:
        st.page_link(
            SETTINGS_PAGE, label=translate("validate.go_to_settings"), icon="⚙️"
        )
    except StreamlitAPIException:
        return
