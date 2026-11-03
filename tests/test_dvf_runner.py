"""Tests for src/real_estate_pipeline/dvf/runner.py."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any
from unittest.mock import call, patch

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from real_estate_pipeline.dvf.load import LoadSummary, load_transactions
from real_estate_pipeline.dvf.models import Transaction
from real_estate_pipeline.dvf.runner import (
    MAX_ERROR_CHARS,
    CommuneResult,
    format_report,
    has_failures,
    load_communes,
    validate_commune_codes,
)

# Three real communes of the metropole: Le Revest, Le Pradet, Carqueiranne.
REVEST, PRADET, CARQUEIRANNE = "83103", "83098", "83034"

_REAL_LOAD = load_transactions


def _transaction(id_mutation: int, **overrides: Any) -> Transaction:
    """A complete Transaction; tests only spell out what they care about."""
    base = Transaction(
        id_mutation=id_mutation,
        date=date(2025, 3, 14),
        year=2025,
        price=250000.0,
        is_vefa=False,
        nature_mutation_code=1,
        nature_mutation_label="Vente",
        property_type_code="1",
        property_type_label="UN APPARTEMENT",
        surface_bati=60.0,
        surface_terrain=0.0,
        rooms=3,
        commune_code="83103",
    )
    return replace(base, **overrides)


def _fake_fetch(
    by_commune: dict[str, list[Transaction] | BaseException],
    calls: list[list[str]] | None = None,
):
    """A fetch that answers from a dict (an exception value is raised) and
    records every list of codes it was called with.
    """

    def fetch(codes: list[str]) -> list[Transaction]:
        if calls is not None:
            calls.append(list(codes))
        (code,) = codes
        outcome = by_commune[code]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return fetch


@pytest.fixture
def sqlite_engine() -> Engine:
    """A real in-memory engine with a `transactions` table shaped like the
    real one (same stand-ins as in test_dvf_load.py).
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "transactions",
        metadata,
        Column("id_mutation", Integer, primary_key=True, autoincrement=False),
        Column("date", Date, nullable=False),
        Column("year", Integer, nullable=False),
        Column("price", Float, nullable=True),
        Column("is_vefa", Boolean, nullable=False),
        Column("nature_mutation_code", Integer, nullable=False),
        Column("nature_mutation_label", String, nullable=False),
        Column("property_type_code", String, nullable=False),
        Column("property_type_label", String, nullable=False),
        Column("surface_bati", Float, nullable=False),
        Column("surface_terrain", Float, nullable=False),
        Column("rooms", Integer, nullable=True),
        Column("commune_code", String, nullable=False),
    )
    metadata.create_all(engine)
    return engine


def _stored_ids(engine: Engine) -> list[int]:
    table = Table("transactions", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        return list(
            connection.execute(select(table.c.id_mutation).order_by(table.c.id_mutation)).scalars()
        )


def _run(codes: list[str], engine: Engine, fetch) -> list[CommuneResult]:
    """load_communes without the courtesy pause, so tests stay instant."""
    return load_communes(codes, engine, fetch=fetch, pause_seconds=0)


# --- the run itself ---


def test_every_commune_is_fetched_once_in_order_and_loaded(sqlite_engine: Engine):
    calls: list[list[str]] = []
    fetch = _fake_fetch(
        {
            REVEST: [_transaction(1), _transaction(2)],
            PRADET: [_transaction(3)],
            CARQUEIRANNE: [_transaction(4), _transaction(5), _transaction(6)],
        },
        calls,
    )

    results = _run([REVEST, PRADET, CARQUEIRANNE], sqlite_engine, fetch)

    assert calls == [[REVEST], [PRADET], [CARQUEIRANNE]]
    assert [r.code for r in results] == [REVEST, PRADET, CARQUEIRANNE]
    assert [r.status for r in results] == ["loaded", "loaded", "loaded"]
    assert [r.fetched for r in results] == [2, 1, 3]
    assert [r.summary for r in results] == [
        LoadSummary(inserted=2, updated=0, unchanged=0),
        LoadSummary(inserted=1, updated=0, unchanged=0),
        LoadSummary(inserted=3, updated=0, unchanged=0),
    ]
    assert _stored_ids(sqlite_engine) == [1, 2, 3, 4, 5, 6]


def test_the_pause_happens_between_communes_only(sqlite_engine: Engine):
    fetch = _fake_fetch(
        {REVEST: [_transaction(1)], PRADET: [_transaction(2)], CARQUEIRANNE: [_transaction(3)]}
    )

    with patch("real_estate_pipeline.dvf.runner.time.sleep") as mock_sleep:
        load_communes([REVEST, PRADET, CARQUEIRANNE], sqlite_engine, fetch=fetch, pause_seconds=7)
        assert mock_sleep.call_args_list == [call(7), call(7)]

        mock_sleep.reset_mock()
        load_communes([REVEST], sqlite_engine, fetch=fetch, pause_seconds=7)
        mock_sleep.assert_not_called()


def test_an_empty_fetch_is_skipped_and_the_run_continues(sqlite_engine: Engine):
    fetch = _fake_fetch({REVEST: [], PRADET: [_transaction(1)]})

    results = _run([REVEST, PRADET], sqlite_engine, fetch)

    assert [r.status for r in results] == ["skipped", "loaded"]
    assert results[0].fetched == 0
    assert results[0].summary is None
    assert _stored_ids(sqlite_engine) == [1]


def test_a_failing_fetch_is_recorded_and_the_next_commune_still_loads(sqlite_engine: Engine):
    fetch = _fake_fetch(
        {REVEST: RuntimeError("HTTP 504 Gateway Time-out"), PRADET: [_transaction(1)]}
    )

    results = _run([REVEST, PRADET], sqlite_engine, fetch)

    assert [r.status for r in results] == ["failed", "loaded"]
    assert results[0].code == REVEST
    assert results[0].error is not None
    assert "RuntimeError" in results[0].error
    assert "504" in results[0].error
    assert _stored_ids(sqlite_engine) == [1]


def test_a_malformed_commune_is_recorded_naming_the_column_and_writes_nothing(
    sqlite_engine: Engine,
):
    broken = [_transaction(1), _transaction(2, surface_bati=float("nan"))]
    fetch = _fake_fetch({REVEST: broken, PRADET: [_transaction(3)]})

    results = _run([REVEST, PRADET], sqlite_engine, fetch)

    assert [r.status for r in results] == ["failed", "loaded"]
    assert results[0].error is not None
    assert "surface_bati" in results[0].error
    assert _stored_ids(sqlite_engine) == [3]  # nothing from the broken commune


def test_a_database_failure_in_one_commune_does_not_stop_the_run(sqlite_engine: Engine):
    calls = {"n": 0}

    def fail_on_first_commune(cleaned, engine):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("simulated", {}, Exception("constraint failed"))
        return _REAL_LOAD(cleaned, engine)

    fetch = _fake_fetch({REVEST: [_transaction(1)], PRADET: [_transaction(2)]})

    with patch(
        "real_estate_pipeline.dvf.runner.load_transactions", side_effect=fail_on_first_commune
    ):
        results = _run([REVEST, PRADET], sqlite_engine, fetch)

    assert [r.status for r in results] == ["failed", "loaded"]
    assert results[0].error is not None
    assert "IntegrityError" in results[0].error
    assert _stored_ids(sqlite_engine) == [2]


def test_ctrl_c_stops_the_run_and_keeps_what_was_already_loaded(sqlite_engine: Engine):
    fetch = _fake_fetch({REVEST: [_transaction(1)], PRADET: KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        _run([REVEST, PRADET], sqlite_engine, fetch)

    assert _stored_ids(sqlite_engine) == [1]


def test_a_long_error_is_cut_to_the_maximum_and_keeps_its_start(sqlite_engine: Engine):
    fetch = _fake_fetch({REVEST: RuntimeError("HTTP 504 " + "x" * 5000)})

    (result,) = _run([REVEST], sqlite_engine, fetch)

    assert result.error is not None
    assert len(result.error) == MAX_ERROR_CHARS
    assert result.error.startswith("RuntimeError: HTTP 504")
    assert result.error.endswith("...")


def test_a_mutation_shared_by_two_communes_is_stored_once(sqlite_engine: Engine):
    """The same mutation can come back through two communes (its commune_code
    is the primary one, not the one queried): the second load finds it
    unchanged, not duplicated.
    """
    shared = _transaction(1)
    fetch = _fake_fetch({REVEST: [shared, _transaction(2)], PRADET: [shared, _transaction(3)]})

    first, second = _run([REVEST, PRADET], sqlite_engine, fetch)

    assert first.summary == LoadSummary(inserted=2, updated=0, unchanged=0)
    assert second.summary == LoadSummary(inserted=1, updated=0, unchanged=1)
    assert _stored_ids(sqlite_engine) == [1, 2, 3]


def test_a_rerun_of_the_whole_load_reports_everything_unchanged(sqlite_engine: Engine):
    fetch = _fake_fetch({REVEST: [_transaction(1), _transaction(2)], PRADET: [_transaction(3)]})
    _run([REVEST, PRADET], sqlite_engine, fetch)

    results = _run([REVEST, PRADET], sqlite_engine, fetch)

    assert [r.summary for r in results] == [
        LoadSummary(inserted=0, updated=0, unchanged=2),
        LoadSummary(inserted=0, updated=0, unchanged=1),
    ]


# --- commune codes ---


def test_unknown_commune_codes_are_refused_before_anything_is_fetched(sqlite_engine: Engine):
    calls: list[list[str]] = []
    fetch = _fake_fetch({REVEST: [_transaction(1)]}, calls)

    with pytest.raises(ValueError, match=r"99999.*abc"):
        _run([REVEST, "99999", "abc"], sqlite_engine, fetch)

    assert calls == []
    assert _stored_ids(sqlite_engine) == []


def test_known_codes_pass_validation_unchanged():
    assert validate_commune_codes([PRADET, REVEST]) == [PRADET, REVEST]


def test_no_commune_means_nothing_to_do(sqlite_engine: Engine):
    calls: list[list[str]] = []

    assert _run([], sqlite_engine, _fake_fetch({}, calls)) == []
    assert calls == []


# --- the report ---


def _mixed_results() -> list[CommuneResult]:
    return [
        CommuneResult(
            code=REVEST,
            status="loaded",
            fetched=10,
            summary=LoadSummary(inserted=7, updated=1, unchanged=2),
        ),
        CommuneResult(code=PRADET, status="skipped"),
        CommuneResult(code=CARQUEIRANNE, status="failed", error="RuntimeError: HTTP 504"),
        CommuneResult(
            code="83137",
            status="loaded",
            fetched=5,
            summary=LoadSummary(inserted=5, updated=0, unchanged=0),
        ),
    ]


def test_the_report_has_a_line_per_commune_correct_totals_and_the_rerun_hint():
    report = format_report(_mixed_results())

    assert "83103  loaded   10 fetched | 7 inserted, 1 updated, 2 unchanged" in report
    assert "83098  SKIPPED  nothing fetched" in report
    assert "83034  FAILED   RuntimeError: HTTP 504" in report
    assert (
        "Total: 2 loaded, 1 skipped, 1 failed | 15 fetched, 12 inserted, 1 updated, 2 unchanged"
        in report
    )
    assert "Re-run the failed communes with: poetry run python scripts/load_dvf.py 83034" in report


def test_a_clean_report_has_no_rerun_hint():
    results = [_mixed_results()[0]]

    assert "Re-run" not in format_report(results)


def test_has_failures_is_true_only_when_a_commune_failed():
    assert has_failures(_mixed_results()) is True
    assert has_failures([_mixed_results()[0], _mixed_results()[1]]) is False
    assert has_failures([]) is False
