"""Tests for src/real_estate_pipeline/common/db.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from real_estate_pipeline.common.db import (
    DB_MAX_RETRIES,
    get_engine,
    run_in_transaction,
)


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


# --- run_in_transaction ---


@pytest.fixture
def sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
    return engine


def _count_items(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM items")).scalar_one()


def test_run_in_transaction_commits_and_returns_operation_result(
    sqlite_engine: Engine,
):
    def operation(connection: Connection) -> str:
        connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'a')"))
        return "done"

    result = run_in_transaction(sqlite_engine, operation)

    assert result == "done"
    assert _count_items(sqlite_engine) == 1


def test_run_in_transaction_rolls_back_everything_if_operation_raises(
    sqlite_engine: Engine,
):
    """All-or-nothing: a failure after a successful write must undo that
    write too.
    """

    def operation(connection: Connection) -> None:
        connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'a')"))
        connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'dup')"))

    with (
        patch("real_estate_pipeline.common.db.time.sleep"),
        pytest.raises(IntegrityError),
    ):
        run_in_transaction(sqlite_engine, operation)

    assert _count_items(sqlite_engine) == 0


def test_run_in_transaction_does_not_retry_integrity_error(sqlite_engine: Engine):
    calls = {"n": 0}

    def operation(connection: Connection) -> None:
        calls["n"] += 1
        connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'a')"))
        connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'dup')"))

    with (
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
        pytest.raises(IntegrityError),
    ):
        run_in_transaction(sqlite_engine, operation)

    assert calls["n"] == 1
    mock_sleep.assert_not_called()


def test_run_in_transaction_retries_operational_error_then_succeeds(
    sqlite_engine: Engine,
):
    calls = {"n": 0}

    def operation(connection: Connection) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("simulated drop", {}, Exception("connection reset"))
        connection.execute(text("INSERT INTO items (id, name) VALUES (1, 'a')"))
        return "ok"

    with patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep:
        result = run_in_transaction(sqlite_engine, operation)

    assert result == "ok"
    assert calls["n"] == 2
    mock_sleep.assert_called_once_with(1)
    assert _count_items(sqlite_engine) == 1


def test_run_in_transaction_backoff_doubles_and_gives_up(sqlite_engine: Engine):
    def operation(connection: Connection) -> None:
        raise OperationalError("simulated drop", {}, Exception("connection reset"))

    with (
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
        pytest.raises(OperationalError),
    ):
        run_in_transaction(sqlite_engine, operation)

    waits = [call.args[0] for call in mock_sleep.call_args_list]
    assert waits == [1, 2]  # 1s, 2s — no sleep after the final attempt
    assert len(waits) == DB_MAX_RETRIES - 1
