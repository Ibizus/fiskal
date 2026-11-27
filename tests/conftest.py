"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import make_engine
from fiskal.ui.session import _session_factory


@pytest.fixture
def session() -> Session:
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def isolated_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cached session factory at a fresh temporary database."""
    monkeypatch.setenv("FISKAL_DB_URL", f"sqlite:///{tmp_path / 'profile.db'}")
    _session_factory.clear()
    yield tmp_path
    _session_factory.clear()
