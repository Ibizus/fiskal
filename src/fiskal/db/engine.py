"""Database engine helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from sqlalchemy import Engine, create_engine, event

DEFAULT_DATA_DIR: Final[str] = "Fiskal"


def get_default_data_dir() -> Path:
    """Return the default OS-specific data directory for Fiskal profiles.

    Returns:
        Path to a user-writable directory outside the repository.
    """
    home = Path.home()
    # Intentionally simple defaults; the caller may override via configuration.
    return home / ".local" / "share" / DEFAULT_DATA_DIR


def make_engine(url: str) -> Engine:
    """Create a SQLite engine with foreign-key enforcement enabled.

    Args:
        url: SQLAlchemy database URL. This helper is intended for SQLite URLs.

    Returns:
        A configured SQLAlchemy engine.
    """
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
