"""Shared plumbing for online metadata APIs (TMDb, OMDb).

Rate-limited, retried with backoff (honouring 429 ``Retry-After``), and every
successful response cached as JSON on disk. When the API is unreachable a
stale cached answer is returned, so the library keeps working offline with
whatever it already fetched. Failures raise the client's ``error`` type (a
subclass of ``MetadataApiError``); callers treat that as "no online metadata
right now", never as fatal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4


class MetadataApiError(Exception):
    pass


class RateLimiter:
    """Simple spacing limiter: at most ``rate`` calls per second, shared by threads."""

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class CachedApi:
    name = "API"
    error: type[MetadataApiError] = MetadataApiError

    def __init__(self, base_url: str, cache_dir: Path, *, headers: dict[str, str] | None = None,
                 params: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None, rate: float = 10.0) -> None:  # fmt: skip
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._params = dict(params or {})
        self._http = httpx.Client(
            base_url=base_url, timeout=15.0, transport=transport,
            headers={"Accept": "application/json", "User-Agent": "PictureHouse", **(headers or {})},
        )  # fmt: skip
        self._limiter = RateLimiter(rate)

    def close(self) -> None:
        self._http.close()

    # ---------------------------------------------------------------- hooks

    def check(self, data: dict[str, Any], path: str) -> dict[str, Any]:
        """Turn an error reported inside a 200 response into ``self.error``."""
        return data

    # ---------------------------------------------------------------- core

    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        key = json.dumps([path, sorted(params.items())], ensure_ascii=False)
        return self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    def get(self, path: str, ttl: float, **params: Any) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        cache = self._cache_path(path, params)
        stale: dict[str, Any] | None = None
        try:
            if cache.exists():
                data = json.loads(cache.read_text(encoding="utf-8"))
                if time.time() - cache.stat().st_mtime < ttl:
                    return data
                stale = data
        except (OSError, ValueError):
            pass
        try:
            data = self._fetch(path, params)
        except self.error:
            if stale is not None:  # offline: old data beats no data
                log.info("%s unreachable, using cached %s", self.name, path)
                return stale
            raise
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache)
        return data

    def _fetch(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last: str = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._limiter.wait()
            try:
                r = self._http.get(path, params={**self._params, **params})
            except httpx.HTTPError as e:
                last = f"network error: {e}"
            else:
                if r.status_code == 200:
                    return self.check(r.json(), path)
                if r.status_code == 401:
                    raise self.error(f"{self.name} rejected the API key (401)")
                if r.status_code == 404:
                    raise self.error(f"{self.name} has no {path}")
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "2") or 2)
                    time.sleep(min(retry_after, 10))
                    continue
                if r.status_code < 500:
                    raise self.error(f"{self.name} request failed: {last}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(0.5 * 2 ** (attempt - 1))  # 0.5, 1, 2 s
        raise self.error(f"{self.name} unavailable after {MAX_ATTEMPTS} attempts ({last})")
