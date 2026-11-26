"""Tests for profile settings and the active interface language."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fiskal import i18n
from fiskal.profile_settings import (
    SettingsError,
    get_language,
    get_setting,
    set_language,
    set_setting,
)


@pytest.fixture(autouse=True)
def _reset_locale() -> None:
    yield
    i18n.set_active_locale(i18n.DEFAULT_LOCALE)


def test_language_defaults_when_unset(session: Session) -> None:
    """An unset language falls back to the default."""
    assert get_language(session) == i18n.DEFAULT_LOCALE


def test_language_round_trip(session: Session) -> None:
    """A saved language is returned and can be changed."""
    set_language(session, "es")
    assert get_language(session) == "es"
    set_language(session, "en")
    assert get_language(session) == "en"


def test_unsupported_language_is_refused(session: Session) -> None:
    """Only supported locales can be saved."""
    with pytest.raises(SettingsError):
        set_language(session, "fr")
    assert get_language(session) == i18n.DEFAULT_LOCALE


def test_stale_stored_language_is_ignored(session: Session) -> None:
    """A stored locale that is not supported falls back to the default."""
    set_setting(session, "language", "fr")
    assert get_language(session) == i18n.DEFAULT_LOCALE
    assert get_setting(session, "missing") is None


def test_translate_uses_active_locale() -> None:
    """Translate follows the active locale unless one is passed."""
    i18n.set_active_locale("es")
    assert i18n.translate("nav.settings") == i18n.translate("nav.settings", "es")
    assert i18n.translate("nav.settings") != i18n.translate("nav.settings", "en")
    assert i18n.translate("nav.settings", "en") == "Settings"
    with pytest.raises(ValueError, match="Unsupported"):
        i18n.set_active_locale("fr")
