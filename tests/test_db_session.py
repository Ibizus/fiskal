"""Tests for the profile database bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from fiskal.db.models import Account
from fiskal.db.session import (
    MIGRATIONS_DIR,
    build_session_factory,
    database_url_from_env,
)


def _head() -> str:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def test_build_session_factory_migrates_new_profile_to_head(tmp_path: Path) -> None:
    """A new profile is created by Alembic and stamped at the head revision."""
    factory = build_session_factory(f"sqlite:///{tmp_path / 'profile.db'}")

    with factory() as session:
        version = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        session.add(Account(name="Demo", currency_code="EUR"))
        session.commit()

    assert version == _head()


def test_build_session_factory_reopens_existing_profile(tmp_path: Path) -> None:
    """Opening an already migrated profile keeps its data."""
    url = f"sqlite:///{tmp_path / 'profile.db'}"
    with build_session_factory(url)() as session:
        session.add(Account(name="Demo", currency_code="EUR"))
        session.commit()

    with build_session_factory(url)() as session:
        assert [a.name for a in session.query(Account).all()] == ["Demo"]


def test_database_url_from_env_skips_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit URL never creates the default profile directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FISKAL_DB_URL", "sqlite:///custom.db")

    assert database_url_from_env() == "sqlite:///custom.db"
    assert not (tmp_path / ".local").exists()
