"""SQLAlchemy session helpers for the Streamlit entry point.

The test suite builds its own in-memory sessions; these helpers wire the pages
to the configured profile database and create the schema when it is missing.
The migration path (Alembic) remains authoritative for evolving an existing
database; ``create_all`` here only bootstraps a brand-new profile.
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import get_default_data_dir, make_engine


def default_database_url() -> str:
    """Return the OS-specific default profile database URL.

    The path is always outside the repository, under the user's data
    directory, so no personal data can land in the worktree.
    """
    data_dir = get_default_data_dir() / "default"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'fiskal.db'}"


def database_url_from_env() -> str:
    """Return the configured profile database URL.

    ``FISKAL_DB_URL`` overrides the default and is what the UI smoke tests set
    to point at a temporary file.
    """
    return os.environ.get("FISKAL_DB_URL", default_database_url())


def build_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Build a session factory and create the schema if it is missing."""
    engine = make_engine(url or database_url_from_env())
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
