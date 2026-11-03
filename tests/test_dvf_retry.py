"""
Tests for dvf/client.py's _get_with_retry(): a failed request is retried with backoff, and the
caller chooses the number of attempts and the timeout.

requests.get and time.sleep are replaced by fakes (no network, no waiting). The real failures
behind these tests: the first request for a large commune ended in an HTTP error after about
60 s on the server side, then succeeded on a later attempt.
"""

import pytest
import requests

from real_estate_pipeline.dvf import client
from real_estate_pipeline.dvf.client import _get_with_retry


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def json(self):
        return self.payload


def gateway_timeout():
    """What the server returned after about 60 s on a cold commune: an HTTP error response."""
    return FakeResponse(error=requests.exceptions.HTTPError("504 Server Error: Gateway Time-out"))


class FakeNetwork:
    """Scripted requests.get: each call consumes the next outcome (an exception to raise, or a
    FakeResponse to return). Records the calls and the sleeps between attempts."""

    def __init__(self):
        self.outcomes: list = []
        self.calls: list[dict] = []
        self.sleeps: list[float] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def network(monkeypatch):
    fake = FakeNetwork()
    monkeypatch.setattr(client.requests, "get", fake.get)
    monkeypatch.setattr(client.time, "sleep", fake.sleeps.append)
    return fake


def test_returns_the_json_body_on_first_success(network):
    network.outcomes = [FakeResponse(payload={"count": 1})]

    assert _get_with_retry("http://x") == {"count": 1}
    assert len(network.calls) == 1
    assert network.sleeps == []


def test_retries_after_failures_and_backs_off(network):
    network.outcomes = [
        requests.exceptions.ReadTimeout("slow"),
        gateway_timeout(),
        FakeResponse(payload={"ok": True}),
    ]

    result = _get_with_retry("http://x", max_attempts=5)

    assert result == {"ok": True}
    assert len(network.calls) == 3
    assert network.sleeps == [client.RETRY_BACKOFF_SECONDS, client.RETRY_BACKOFF_SECONDS * 2]


def test_http_errors_are_retried_too(network):
    """Cold-commune failures were HTTP errors from the server, not client-side timeouts."""
    network.outcomes = [gateway_timeout(), FakeResponse(payload={"ok": True})]

    assert _get_with_retry("http://x", max_attempts=2) == {"ok": True}
    assert len(network.calls) == 2


def test_gives_up_after_max_attempts_and_raises_the_last_error(network):
    network.outcomes = [requests.exceptions.ReadTimeout(f"failure {n}") for n in (1, 2, 3, 4)]

    with pytest.raises(requests.exceptions.ReadTimeout, match="failure 4"):
        _get_with_retry("http://x", max_attempts=4)

    assert len(network.calls) == 4
    assert len(network.sleeps) == 3  # no pointless wait after the final attempt


def test_more_attempts_than_the_default_are_honoured(network):
    """The cold-commune case: four failures, then success on the fifth attempt, which the
    default of three attempts could never reach."""
    network.outcomes = [gateway_timeout() for _ in range(4)] + [FakeResponse(payload={"ok": True})]

    assert _get_with_retry("http://x", max_attempts=5) == {"ok": True}
    assert len(network.calls) == 5


def test_a_single_attempt_does_not_retry(network):
    network.outcomes = [requests.exceptions.ReadTimeout("slow")]

    with pytest.raises(requests.exceptions.ReadTimeout):
        _get_with_retry("http://x", max_attempts=1)

    assert len(network.calls) == 1
    assert network.sleeps == []


def test_default_attempts_come_from_max_retries(network):
    network.outcomes = [requests.exceptions.ReadTimeout("slow")] * client.MAX_RETRIES

    with pytest.raises(requests.exceptions.ReadTimeout):
        _get_with_retry("http://x")

    assert len(network.calls) == client.MAX_RETRIES


def test_timeout_is_passed_to_every_attempt(network):
    network.outcomes = [requests.exceptions.ReadTimeout("slow"), FakeResponse(payload={})]

    _get_with_retry("http://x", timeout=65, max_attempts=2)

    assert [call["timeout"] for call in network.calls] == [65, 65]
