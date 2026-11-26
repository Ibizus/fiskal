"""SQLAlchemy session helpers for the Streamlit entry point.

The test suite builds its own in-memory sessions; these helpers wire the pages
to the configured profile database and bring its schema to the latest Alembic
revision, so a profile created by the UI can keep being migrated later.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.engine import get_default_data_dir, make_engine

MIGRATIONS_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "migrations"


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
    return os.environ.get("FISKAL_DB_URL") or default_database_url()


def upgrade_database(url: str) -> None:
    """Apply every pending Alembic migration to the database at ``url``."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


def build_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Build a session factory on a database migrated to the latest revision."""
    resolved_url = url or database_url_from_env()
    upgrade_database(resolved_url)
    return sessionmaker(bind=make_engine(resolved_url))
