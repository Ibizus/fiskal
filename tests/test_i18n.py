"""Tests for the translation catalog."""

import pytest

from fiskal import i18n


def test_all_locales_supported() -> None:
    """English and Spanish must both be supported."""
    assert "en" in i18n.SUPPORTED_LOCALES
    assert "es" in i18n.SUPPORTED_LOCALES


def test_catalog_key_parity() -> None:
    """Every key present in English must also exist in Spanish and vice versa."""
    en_keys = i18n.list_keys("en")
    es_keys = i18n.list_keys("es")
    missing_in_spanish = en_keys - es_keys
    missing_in_english = es_keys - en_keys
    assert not missing_in_spanish, f"Keys missing in Spanish: {missing_in_spanish}"
    assert not missing_in_english, f"Keys missing in English: {missing_in_english}"


@pytest.mark.parametrize("locale", ["en", "es"])
def test_translate_returns_text(locale: str) -> None:
    """Known keys resolve to non-empty text in every supported locale."""
    text = i18n.translate("app.title", locale=locale)
    assert text
    assert isinstance(text, str)
