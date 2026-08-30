"""Tests for src/real_estate_pipeline/common/db.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.engine import Engine

from real_estate_pipeline.common.db import get_engine


def test_get_engine_returns_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_engine() must return a real Engine instance.

    No real DB connection is made here — create_engine() only builds the
    engine object; it doesn't actually connect until first used.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")

    engine = get_engine()

    assert isinstance(engine, Engine)


def test_get_engine_enables_pre_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_engine() must pass pool_pre_ping=True to create_engine, so a
    stale pooled connection is caught before use rather than failing
    mid-query. Checked via the actual call arguments rather than reaching
    into SQLAlchemy's internal pool attributes.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")

    with patch("real_estate_pipeline.common.db.create_engine") as mock_create_engine:
        get_engine()

    _, kwargs = mock_create_engine.call_args
    assert kwargs.get("pool_pre_ping") is True


def test_get_engine_raises_if_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing DATABASE_URL should fail loudly (KeyError) rather than
    silently falling back to some default connection.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(KeyError):
        get_engine()
