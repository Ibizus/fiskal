"""Translation catalog support for Fiskal.

All user-facing strings are loaded from JSON catalogs keyed in neutral English.
Code never contains Spanish display text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

CATALOG_DIR: Final[Path] = Path(__file__).parent
SUPPORTED_LOCALES: Final[frozenset[str]] = frozenset({"en", "es"})
DEFAULT_LOCALE: Final[str] = "en"


def _load_catalog(locale: str) -> dict[str, str]:
    """Load the translation catalog for ``locale``.

    Args:
        locale: Two-letter locale code.

    Returns:
        Mapping of message keys to translated display text.

    Raises:
        ValueError: If the locale is not supported.
        FileNotFoundError: If the catalog file is missing.
    """
    if locale not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported locale: {locale}")
    path = CATALOG_DIR / f"{locale}.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise TypeError(f"Catalog {locale} must be a JSON object")
    return {str(key): str(value) for key, value in data.items()}


def get_catalog(locale: str = DEFAULT_LOCALE) -> dict[str, str]:
    """Return the full translation catalog for ``locale``.

    Args:
        locale: Two-letter locale code.

    Returns:
        Mapping of message keys to translated display text.
    """
    return _load_catalog(locale)


def translate(key: str, locale: str = DEFAULT_LOCALE) -> str:
    """Return the display text for ``key`` in ``locale``.

    Args:
        key: Neutral English message key.
        locale: Two-letter locale code.

    Returns:
        Translated text, or ``key`` wrapped in brackets if missing.
    """
    catalog = _load_catalog(locale)
    return catalog.get(key, f"[{key}]")


def list_keys(locale: str = DEFAULT_LOCALE) -> set[str]:
    """Return all message keys defined for ``locale``.

    Args:
        locale: Two-letter locale code.

    Returns:
        Set of message keys.
    """
    return set(_load_catalog(locale).keys())
