"""Database engine and session factory helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from fiskal.db.base import Base

DEFAULT_DATA_DIR: Final[str] = "Fiskal"


def get_default_data_dir() -> Path:
    """Return the default OS-specific data directory for Fiskal profiles.

    Returns:
        Path to a user-writable directory outside the repository.
    """
    home = Path.home()
    # Intentionally simple defaults; the caller may override via configuration.
    return home / ".local" / "share" / DEFAULT_DATA_DIR


def make_engine(database_path: Path) -> Engine:
    """Create a SQLite engine pointing at ``database_path``.

    Args:
        database_path: Filesystem location for the SQLite database.

    Returns:
        Configured SQLAlchemy engine.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{database_path}", echo=False)


SessionLocal = sessionmaker()


def init_database(engine: Engine) -> None:
    """Create all tables registered on ``Base``.

    Args:
        engine: Engine bound to a database.
    """
    Base.metadata.create_all(bind=engine)
