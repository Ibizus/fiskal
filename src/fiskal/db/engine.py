"""Database location helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Final

DEFAULT_DATA_DIR: Final[str] = "Fiskal"


def get_default_data_dir() -> Path:
    """Return the default OS-specific data directory for Fiskal profiles.

    Returns:
        Path to a user-writable directory outside the repository.
    """
    home = Path.home()
    # Intentionally simple defaults; the caller may override via configuration.
    return home / ".local" / "share" / DEFAULT_DATA_DIR
