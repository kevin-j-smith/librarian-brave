"""The Brave backend, against replies shaped like Brave's documented ones.
No request leaves the machine."""
from pathlib import Path

import httpx
import pytest

from librarian_brave import backend as brave
from librarian_brave.backend import BraveBackend

BODY = {"web": {"results": [
    {"url": "https://www.energy.gov/solar-batteries", "title": "Solar batteries", "description": "How to size."},
    {"url": "https://example.org/b", "title": "", "description": None},
    {"title": "no url"}]}}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    from librarian.config import settings as core
    monkeypatch.setattr(core, "search_cache_enabled", False)
    monkeypatch.setattr(core, "search_cache_dir", str(tmp_path))
    monkeypatch.setenv("BRAVE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_MONTHLY_QUOTA", raising=False)
    brave.reset()
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        return calls_reply[0]
    calls_reply = [None]
    monkeypatch.setattr(httpx, "get", get)
    yield calls, calls_reply
    brave.reset()


class Reply:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body = status, body or {}
        self.headers = httpx.Headers(headers or {})

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


def test_without_a_key_nothing_is_sent(hermetic):
    calls, _ = hermetic
    resp = BraveBackend().search("q", 10)
    assert resp.results == [] and resp.degraded == [("brave", "BRAVE_API_KEY is not set")]
    assert calls == [] and BraveBackend().diagnose()["refusing"]


def test_results_with_the_key_and_the_month_counted(hermetic, monkeypatch):
    calls, reply = hermetic
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    reply[0] = Reply(body=BODY, headers={"X-RateLimit-Remaining": "0, 998"})
    resp = BraveBackend().search("solar battery", 50)
    assert [r.url for r in resp.results] == ["https://www.energy.gov/solar-batteries", "https://example.org/b"]
    assert resp.results[1].title == "https://example.org/b" and resp.degraded == []
    assert calls[0]["headers"]["X-Subscription-Token"] == "bk" and calls[0]["params"]["count"] == 20
    assert brave.used_this_month() == 1
    with pytest.raises(ValueError):
        BraveBackend().search("q", 5, "video")


def test_the_monthly_quota_refuses_before_any_request(hermetic, monkeypatch):
    calls, reply = hermetic
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    monkeypatch.setenv("BRAVE_MONTHLY_QUOTA", "2")
    reply[0] = Reply(body=BODY)
    BraveBackend().search("a", 5)
    BraveBackend().search("b", 5)
    resp = BraveBackend().search("c", 5)
    assert len(calls) == 2 and "BRAVE_MONTHLY_QUOTA (2) reached" in resp.degraded[0][1]


def test_braves_own_count_stops_requests_until_its_reset(hermetic, monkeypatch):
    calls, reply = hermetic
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    reply[0] = Reply(body=BODY, headers={"X-RateLimit-Limit": "1, 2000", "X-RateLimit-Remaining": "1, 0",
                                         "X-RateLimit-Reset": "1, 86400"})
    assert BraveBackend().search("a", 5).results
    resp = BraveBackend().search("b", 5)
    assert len(calls) == 1 and "quota is spent" in resp.degraded[0][1]


def test_refusals_are_degraded_not_raised(hermetic, monkeypatch):
    _, reply = hermetic
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    reply[0] = Reply(status=401)
    assert "refused" in BraveBackend().search("a", 5).degraded[0][1]
    reply[0] = Reply(status=429, headers={"X-RateLimit-Reset": "1, 30"})
    assert "429" in BraveBackend().search("b", 5).degraded[0][1]


def test_a_cached_reply_costs_no_request_and_no_quota(hermetic, monkeypatch):
    from librarian.config import settings as core
    calls, reply = hermetic
    monkeypatch.setattr(core, "search_cache_enabled", True)
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    reply[0] = Reply(body=BODY)
    BraveBackend().search("same", 10)
    monkeypatch.delenv("BRAVE_API_KEY")
    assert BraveBackend().search("same", 10).results           # replayed, even without the key
    assert len(calls) == 1 and brave.used_this_month() == 1
    cached = next(p for p in Path(core.search_cache_dir).rglob("*.json") if p.name != "usage.json")
    assert "bk" not in cached.read_text()


def test_it_is_a_librarian_backend():
    from librarian.research.backends import BACKEND_API_VERSION, SearchBackend
    assert isinstance(BraveBackend(), SearchBackend) and BraveBackend.api_version == BACKEND_API_VERSION


def test_a_plan_without_a_monthly_cap_is_never_read_as_spent(hermetic, monkeypatch):
    """The headers a live key returned on 2026-10-01: 50 a second, no monthly cap."""
    calls, reply = hermetic
    monkeypatch.setenv("BRAVE_API_KEY", "bk")
    reply[0] = Reply(body=BODY, headers={"X-RateLimit-Limit": "50, 0", "X-RateLimit-Remaining": "49, 0",
                                         "X-RateLimit-Reset": "1, 2589511",
                                         "X-RateLimit-Policy": "50;w=1, 0;w=2678400"})
    assert BraveBackend().search("a", 5).results
    assert BraveBackend().search("b", 5).results
    assert len(calls) == 2 and not BraveBackend().diagnose()["refusing"]
