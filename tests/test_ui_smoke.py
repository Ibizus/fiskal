"""Smoke tests for the Streamlit user interface.

These tests use Streamlit's headless app testing support so they run without a
browser and exercise the real page code.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

LAUNCHER = Path(__file__).resolve().parents[1] / "src" / "fiskal" / "ui" / "launcher.py"


def test_welcome_page_renders() -> None:
    """The welcome page runs without exceptions and shows the expected content."""
    app = AppTest.from_file(str(LAUNCHER)).run()

    assert not app.exception, f"App raised an exception: {app.exception}"

    titles = [t.value for t in app.title]
    assert "Fiskal" in titles, f"Expected 'Fiskal' title, got {titles}"

    subheaders = [s.value for s in app.subheader]
    assert "Local-first personal finance" in subheaders, (
        f"Expected tagline, got {subheaders}"
    )
