"""Smoke tests that all skeleton packages import cleanly."""

from fiskal import classification, domain, importers, reports, ui
from fiskal.db import base, engine
from fiskal.domain import models


def test_domain_imports() -> None:
    """Domain submodules import without errors."""
    assert domain is not None
    assert models is not None


def test_classification_imports() -> None:
    """Classification package imports without errors."""
    assert classification is not None


def test_importers_import() -> None:
    """Importers package imports without errors."""
    assert importers is not None


def test_reports_import() -> None:
    """Reports package imports without errors."""
    assert reports is not None


def test_ui_import() -> None:
    """UI package imports without errors."""
    assert ui is not None


def test_db_imports() -> None:
    """Database helpers import without errors."""
    assert base is not None
    assert engine is not None
