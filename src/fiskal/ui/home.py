"""Home page: pending movements, the last import and links to the other pages."""

from __future__ import annotations

import streamlit as st

from fiskal.i18n import translate
from fiskal.services import home_overview
from fiskal.ui.messages import show_flashes
from fiskal.ui.session import open_session

# Page files relative to the launcher, with their navigation label keys.
WORKING_PAGES: tuple[tuple[str, str, str], ...] = (
    ("pages/1_Import.py", "nav.import", "📥"),
    ("pages/2_Validate.py", "nav.validate", "✅"),
    ("pages/3_Transactions.py", "nav.transactions", "🧾"),
    ("pages/4_Settings.py", "nav.settings", "⚙️"),
)


def render_home() -> None:
    """Render the Home page."""
    st.header(translate("nav.home"))
    show_flashes()
    with open_session() as session:
        overview = home_overview(session)

    pending_col, import_col = st.columns(2)
    pending_col.metric(
        translate("home.pending"),
        overview.pending_count,
        help=translate("home.pending_help"),
    )
    last_import = (
        f"{overview.last_import_at:%Y-%m-%d}"
        if overview.last_import_at is not None
        else translate("home.no_import")
    )
    import_col.metric(translate("home.last_import"), last_import)

    st.subheader(translate("home.links"))
    for path, label_key, icon in WORKING_PAGES:
        st.page_link(path, label=translate(label_key), icon=icon)
