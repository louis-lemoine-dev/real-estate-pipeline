"""Tests for dvf/orchestrator.py's pass-through of the paging options to fetch_mutations()."""

from real_estate_pipeline.dvf import orchestrator
from real_estate_pipeline.dvf.orchestrator import fetch_transactions


def record_calls(monkeypatch):
    calls = []

    def fake_fetch_mutations(code, max_pages=None, allow_partial=False):
        calls.append((code, max_pages, allow_partial))
        return []

    monkeypatch.setattr(orchestrator, "fetch_mutations", fake_fetch_mutations)
    return calls


def test_defaults_ask_for_a_complete_fetch(monkeypatch):
    calls = record_calls(monkeypatch)

    fetch_transactions(["83137"])

    assert calls == [("83137", None, False)]


def test_sampling_options_are_passed_through(monkeypatch):
    calls = record_calls(monkeypatch)

    fetch_transactions(["83137"], max_pages_per_commune=2, allow_partial=True)

    assert calls == [("83137", 2, True)]
