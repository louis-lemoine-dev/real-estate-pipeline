"""
Tests for dvf/client.py's fetch_mutations(): a commune is fetched completely, or the call fails.

The API is replaced by fake pages (no network). The scenario behind these tests is real: the old
default cap of 10 pages silently cut Carqueiranne from 3,159 mutations to 1,000.
"""

import pytest

from real_estate_pipeline.dvf import client
from real_estate_pipeline.dvf.client import IncompleteFetchError, fetch_mutations

PAGE_SIZE = 100


class FakeApi:
    """Serves pre-built pages and records every request made to it."""

    def __init__(self):
        self.pages: list[dict] = []
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None, max_attempts=None):
        self.calls.append(
            {"url": url, "params": params, "timeout": timeout, "max_attempts": max_attempts}
        )
        # The first request carries the query params; later ones follow the `next` link.
        index = 0 if params is not None else int(url.rsplit("page=", 1)[1]) - 1
        return self.pages[index]


def make_pages(ids, *, count=None, endless=False):
    """Split `ids` into pages of PAGE_SIZE rows. Every page links to the next one, except the
    last (unless `endless`, which keeps a dangling `next` to simulate a runaway pagination).
    `count` is what the API reports; it defaults to the real number of ids."""
    chunks = [ids[i : i + PAGE_SIZE] for i in range(0, len(ids), PAGE_SIZE)] or [[]]
    reported = len(ids) if count is None else count
    pages = []
    for number, chunk in enumerate(chunks, start=1):
        is_last = number == len(chunks)
        has_next = endless or not is_last
        pages.append(
            {
                "count": reported,
                "next": f"http://fake/mutations/?page={number + 1}" if has_next else None,
                "results": [{"idmutation": i} for i in chunk],
            }
        )
    return pages


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(client, "_get_with_retry", fake.get)
    monkeypatch.setattr(client, "RATE_LIMIT_SECONDS", 0)
    return fake


def test_complete_walk_returns_every_row_in_order(api):
    api.pages = make_pages(list(range(250)))  # 3 pages

    rows = fetch_mutations("83137")

    assert [r["idmutation"] for r in rows] == list(range(250))
    assert len(api.calls) == 3
    assert api.calls[0]["params"] == {"code_insee": "83137", "fields": "all"}
    assert api.calls[1]["params"] is None
    assert api.calls[1]["url"].startswith("https://")  # the client upgrades http -> https


def test_default_fetches_past_the_old_ten_page_cap(api):
    """Regression for the real bug: 1,500 rows is 15 pages, and the old cap stopped at 10."""
    api.pages = make_pages(list(range(1500)))

    rows = fetch_mutations("83137")

    assert len(rows) == 1500
    assert len(api.calls) == 15


def test_explicit_ceiling_reached_raises(api):
    api.pages = make_pages(list(range(250)))

    with pytest.raises(IncompleteFetchError, match="page ceiling"):
        fetch_mutations("83137", max_pages=2)


def test_allow_partial_returns_what_was_fetched_at_the_ceiling(api):
    api.pages = make_pages(list(range(250)))

    rows = fetch_mutations("83137", max_pages=2, allow_partial=True)

    assert len(rows) == 200
    assert len(api.calls) == 2


def test_count_mismatch_raises(api):
    """The API says 250 but only 240 distinct rows come back: a skipped row must not pass."""
    api.pages = make_pages(list(range(240)), count=250)

    with pytest.raises(IncompleteFetchError, match="reports 250 mutations but 240"):
        fetch_mutations("83137")


def test_duplicate_rows_across_pages_are_dropped(api):
    """Unstable pagination can repeat a row on two pages; it is kept once, and the count
    still matches the distinct total."""
    ids = list(range(199)) + [99]  # id 99 appears twice (pages 1 and 2)
    api.pages = make_pages(ids, count=199)

    rows = fetch_mutations("83137")

    assert len(rows) == 199
    assert [r["idmutation"] for r in rows].count(99) == 1


def test_duplicate_that_hides_a_missing_row_still_raises(api):
    """200 rows fetched, but one is a repeat: 199 distinct vs a reported 200 must fail."""
    ids = list(range(199)) + [99]
    api.pages = make_pages(ids, count=200)

    with pytest.raises(IncompleteFetchError, match="reports 200 mutations but 199"):
        fetch_mutations("83137")


def test_missing_count_raises(api):
    api.pages = make_pages(list(range(50)))
    del api.pages[0]["count"]

    with pytest.raises(IncompleteFetchError, match="no 'count' field"):
        fetch_mutations("83137")


def test_empty_commune_returns_empty_list(api):
    api.pages = make_pages([])

    assert fetch_mutations("83137") == []


def test_runaway_pagination_hits_the_derived_ceiling(api):
    """The API keeps offering a next page although `count` says one page is enough."""
    api.pages = make_pages(list(range(300)), count=100, endless=True)

    with pytest.raises(IncompleteFetchError, match="page ceiling"):
        fetch_mutations("83137")

    assert len(api.calls) == 1 + client.PAGE_CEILING_MARGIN


def test_first_request_is_more_patient_than_later_pages(api, monkeypatch):
    monkeypatch.setattr(client, "FIRST_REQUEST_TIMEOUT_SECONDS", 111)
    monkeypatch.setattr(client, "FIRST_REQUEST_MAX_ATTEMPTS", 7)
    monkeypatch.setattr(client, "REQUEST_TIMEOUT_SECONDS", 22)
    api.pages = make_pages(list(range(250)))

    fetch_mutations("83137")

    assert [call["timeout"] for call in api.calls] == [111, 22, 22]
    # Later pages leave max_attempts unset, so they use the normal default.
    assert [call["max_attempts"] for call in api.calls] == [7, None, None]
