"""Interface language: apply the profile's choice and offer the selector."""

from __future__ import annotations

import streamlit as st

from fiskal.i18n import SUPPORTED_LOCALES, set_active_locale, translate
from fiskal.profile_settings import get_language, set_language
from fiskal.ui.session import open_session

_SELECTOR_KEY = "language_selector"


def apply_profile_language() -> None:
    """Make the profile's saved language the active one for this run."""
    with open_session() as session:
        set_active_locale(get_language(session))


def _save_language() -> None:
    chosen = st.session_state[_SELECTOR_KEY]
    with open_session() as session:
        set_language(session, chosen)


def render_language_selector() -> None:
    """Show the language selector in the sidebar; a change is saved at once."""
    with open_session() as session:
        current = get_language(session)
    options = sorted(SUPPORTED_LOCALES)
    with st.sidebar:
        st.selectbox(
            translate("language.label"),
            options,
            index=options.index(current),
            format_func=lambda locale: translate(f"language.{locale}", locale),
            key=_SELECTOR_KEY,
            on_change=_save_language,
        )
