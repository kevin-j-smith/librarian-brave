"""`BraveBackend`: librarian's search-backend interface over the Brave Search API.

Registered under the `librarian.search_backends` entry point as "brave". It
answers the `web` category, and is meant to be routed beside another web
backend for an independent index:

    RESEARCH_SEARCH_ROUTES="web: searxng, brave; video: searxng; science: searxng"

Every query goes to Brave under your account, which is why this is a
separate, opt-in plugin, and why it does nothing at all until BRAVE_API_KEY
is set: without a key a search returns no results and says why (degraded),
and no request is made. Measured live (October 1, 2026): a query returned 10
on-topic results, and the plan's headers showed 50 requests a second and no
monthly cap.

**The monthly quota** (BRAVE_MONTHLY_QUOTA, default 1000 requests a calendar
month, UTC) is counted here, in a small file beside librarian's search cache,
and a search past it is refused before any request, reported as degraded.
Brave's own remaining-quota header is read too: when it says the month is
spent, searches stop until the reset it names. A cached reply (librarian's
search cache) costs nothing and isn't counted. Requests are paced
(BRAVE_MIN_INTERVAL_SECONDS, 1.1, under the free plan's 1 a second).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from librarian.config import settings as core
from librarian.research import search_cache
from librarian.research.backends import BACKEND_API_VERSION, SearchResponse
from librarian.research.providers import SearchResult
from librarian.research.search_cache import CachePolicy

from . import __version__

logger = logging.getLogger("librarian_brave")

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
NAMESPACE = "brave-v1"
_MAX_PER_REQUEST = 20

_lock = threading.Lock()
_next_allowed = 0.0
_spent_until = 0.0       # Brave said the month's quota is spent, until this time


def _setting(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def api_key() -> str:
    return _setting("BRAVE_API_KEY", "")


def monthly_quota() -> int:
    return int(_setting("BRAVE_MONTHLY_QUOTA", "1000"))


def min_interval() -> float:
    return float(_setting("BRAVE_MIN_INTERVAL_SECONDS", "1.1"))


def _usage_path() -> Path:
    return Path(core.search_cache_dir) / NAMESPACE / "usage.json"


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def used_this_month() -> int:
    try:
        return int(json.loads(_usage_path().read_text(encoding="utf-8")).get(_month(), 0))
    except (OSError, ValueError):
        return 0


def _count_request() -> None:
    path = _usage_path()
    with _lock:
        try:
            usage = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            usage = {}
        usage[_month()] = int(usage.get(_month(), 0)) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(usage), encoding="utf-8")
        os.replace(tmp, path)


def _pace() -> None:
    global _next_allowed
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _next_allowed - now)
        _next_allowed = max(now, _next_allowed) + min_interval()
    if wait:
        time.sleep(wait)


def _numbers(value: Optional[str]) -> list[float]:
    """Brave's rate-limit headers list one value per window, shortest first
    ("50, 0"); unreadable parts are dropped."""
    out = []
    for part in (value or "").split(","):
        try:
            out.append(float(part.strip()))
        except ValueError:
            pass
    return out


def _read_quota_headers(resp: httpx.Response) -> None:
    """Stop requests until the reset when Brave says the month is spent.

    A window whose limit is 0 has no cap. Measured on a live key
    (2026-10-01): `x-ratelimit-limit: 50, 0`, `x-ratelimit-remaining: 49, 0`,
    `x-ratelimit-policy: 50;w=1, 0;w=2678400`, a plan with 50 requests a
    second and no monthly cap. Reading the month's remaining 0 alone, as the
    first version did, would have stopped all requests for the 30 days
    after the first one.
    """
    global _spent_until
    limits = _numbers(resp.headers.get("x-ratelimit-limit"))
    remaining = _numbers(resp.headers.get("x-ratelimit-remaining"))
    resets = _numbers(resp.headers.get("x-ratelimit-reset"))
    if len(limits) < 2 or len(remaining) < 2:
        return
    if limits[-1] > 0 and remaining[-1] <= 0:
        reset = resets[-1] if len(resets) >= 2 else 3600.0
        _spent_until = time.time() + reset
        logger.warning("Brave says this month's quota is spent; no requests for %.0f s", reset)


class BraveBackend:
    name = "brave"
    api_version = BACKEND_API_VERSION
    categories = frozenset({"web"})

    def search(self, query: str, count: int, category: str = "web", *,
               cache: Optional[CachePolicy] = None) -> SearchResponse:
        global _spent_until
        if category not in self.categories:
            raise ValueError(f"brave backend has no category {category!r}")
        params = {"q": query, "count": max(1, min(count, _MAX_PER_REQUEST))}
        hit = search_cache.lookup(ENDPOINT, params, NAMESPACE, cache)
        if hit is not None:
            return SearchResponse(self._results(hit))
        key = api_key()
        if not key:
            return SearchResponse([], [("brave", "BRAVE_API_KEY is not set")])
        if time.time() < _spent_until:
            return SearchResponse([], [("brave", "the month's quota is spent (Brave's count)")])
        if used_this_month() >= monthly_quota():
            return SearchResponse([], [("brave", f"BRAVE_MONTHLY_QUOTA ({monthly_quota()}) reached "
                                                 f"for {_month()}")])
        _pace()
        _count_request()
        resp = httpx.get(ENDPOINT, params=params, timeout=30, headers={
            "X-Subscription-Token": key, "Accept": "application/json",
            "User-Agent": f"librarian-brave/{__version__}"})
        _read_quota_headers(resp)
        if resp.status_code == 429:
            resets = _numbers(resp.headers.get("x-ratelimit-reset"))
            _spent_until = max(_spent_until, time.time() + (resets[0] if resets else 60.0))
            return SearchResponse([], [("brave", "HTTP 429: rate limited")])
        if resp.status_code in (401, 403):
            return SearchResponse([], [("brave", f"HTTP {resp.status_code}: the API key was refused")])
        resp.raise_for_status()
        payload = resp.json()
        search_cache.store(ENDPOINT, params, NAMESPACE, payload, cache)
        return SearchResponse(self._results(payload))

    @staticmethod
    def _results(payload: dict) -> list[SearchResult]:
        return [SearchResult(url=r["url"], title=r.get("title") or r["url"],
                             snippet=r.get("description") or "")
                for r in ((payload.get("web") or {}).get("results") or []) if r.get("url")]

    def session(self):
        return nullcontext(self)

    def diagnose(self, category: str = "web") -> dict:
        """Why Brave would refuse right now. Makes no request."""
        why = None
        if not api_key():
            why = "BRAVE_API_KEY is not set"
        elif time.time() < _spent_until:
            why = "the month's quota is spent (Brave's count)"
        elif used_this_month() >= monthly_quota():
            why = f"BRAVE_MONTHLY_QUOTA ({monthly_quota()}) reached"
        return {"refusing": [("brave", why)] if why else [], "silent": []}


def reset() -> None:
    """Forget pacing and Brave's spent flag (tests)."""
    global _next_allowed, _spent_until
    _next_allowed = 0.0
    _spent_until = 0.0
