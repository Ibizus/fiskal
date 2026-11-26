"""Streamlit bootstrap that provides a cached SQLAlchemy session factory.

Each page opens a session through :func:`open_session`. The factory, the schema
and the neutral seed are created once per process thanks to
``st.cache_resource``.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session, sessionmaker

from fiskal.classification import seed_neutral_data
from fiskal.db.session import build_session_factory, database_url_from_env


@st.cache_resource
def _session_factory() -> sessionmaker[Session]:
    factory = build_session_factory(database_url_from_env())
    with factory() as session:
        seed_neutral_data(session)
    return factory


def open_session() -> Session:
    """Open a session on the profile database."""
    return _session_factory()()
