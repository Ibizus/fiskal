"""Placeholder welcome page."""

from __future__ import annotations

import streamlit as st

from fiskal.i18n import translate

st.title(translate("app.title"))
st.subheader(translate("app.tagline"))
st.write(translate("ui.no_data"))
