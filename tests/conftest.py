"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fiskal.db.base import Base
from fiskal.db.engine import make_engine


@pytest.fixture
def session() -> Session:
    """Return a session on an empty in-memory database with FKs enabled."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
