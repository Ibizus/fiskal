"""Profile-level settings stored in the profile database.

Settings live next to the owner's data, never in the repository. Today the only
setting is the interface language; when it is unset the application keeps its
default behavior (English).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fiskal.db.models import ProfileSetting
from fiskal.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES

LANGUAGE_KEY = "language"


class SettingsError(Exception):
    """Raised when a setting value is not acceptable."""


def get_setting(session: Session, key: str) -> str | None:
    """Return the stored value for ``key``, or ``None`` when it is unset."""
    row = session.get(ProfileSetting, key)
    return row.value if row is not None else None


def set_setting(session: Session, key: str, value: str) -> None:
    """Store ``value`` under ``key``, replacing any previous value."""
    row = session.get(ProfileSetting, key)
    if row is None:
        session.add(ProfileSetting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def get_language(session: Session) -> str:
    """Return the profile language, falling back to the default when unset.

    A stored value that is no longer a supported locale is ignored.
    """
    stored = get_setting(session, LANGUAGE_KEY)
    if stored in SUPPORTED_LOCALES:
        return str(stored)
    return DEFAULT_LOCALE


def set_language(session: Session, locale: str) -> None:
    """Remember ``locale`` as the profile language."""
    if locale not in SUPPORTED_LOCALES:
        raise SettingsError(f"unsupported language: {locale}")
    set_setting(session, LANGUAGE_KEY, locale)
